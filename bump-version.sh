#!/usr/bin/env bash
# Neue Version veröffentlichen: fragt, wie versioniert werden soll (patch/minor/major/eigene),
# setzt die Version in Firmware und Server, testet, mergt devel -> main, setzt Tag vX.Y.Z und pusht.
# Der Tag startet den GitHub-Workflow: Docker-Image (ghcr) + GitHub-Release mit ESP32-Images.
#
#   ./bump-version.sh          # mit Abfrage
#   ./bump-version.sh --dry    # alles lokal, nichts pushen
set -euo pipefail

DRY="${1:-}"
FW=esp32/avinox-sync/CMakeLists.txt
PY=server/pyproject.toml

ask_version() {
  local current="$1" major minor patch choice custom
  if [[ ! "$current" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "❌ Unlesbare Version: $current" >&2; return 1
  fi
  IFS='.' read -r major minor patch <<< "$current"
  {
    echo ""
    echo "Aktuelle Version: ${current}"
    echo ""
    echo "  1) patch  -> ${major}.${minor}.$((patch + 1))  (Default)"
    echo "  2) minor  -> ${major}.$((minor + 1)).0"
    echo "  3) major  -> $((major + 1)).0.0"
    echo "  4) eigene Version eingeben"
    echo "  5) abbrechen"
    echo ""
  } >&2
  read -r -p "> " choice < /dev/tty
  case "${choice:-1}" in
    1|patch) echo "${major}.${minor}.$((patch + 1))" ;;
    2|minor) echo "${major}.$((minor + 1)).0" ;;
    3|major) echo "$((major + 1)).0.0" ;;
    4)
      read -r -p "Neue Version (X.Y.Z): " custom < /dev/tty
      [[ "$custom" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "❌ Ungültiges Format, erwartet X.Y.Z" >&2; return 1; }
      echo "$custom" ;;
    5|q|abbrechen) echo "Abgebrochen." >&2; return 1 ;;
    *) echo "❌ Ungültige Auswahl: $choice" >&2; return 1 ;;
  esac
}

cd "$(git rev-parse --show-toplevel)"

[[ -z "$(git status --porcelain)" ]] || { echo "❌ Uncommittete Änderungen – erst committen." >&2; exit 1; }

echo "🔄 Hole Stand von GitHub ..."
git fetch -q origin --tags
git switch -q devel
git merge -q --ff-only origin/devel || { echo "❌ devel weicht von origin/devel ab – erst abgleichen." >&2; exit 1; }
if [[ -n "$(git log --oneline --no-merges devel..origin/main)" ]]; then
  echo "❌ origin/main hat Commits, die devel fehlen – erst nach devel mergen." >&2; exit 1
fi

CURRENT="$(sed -nE 's/.*set\(PROJECT_VER "([^"]+)"\).*/\1/p' "$FW")"
NEW="$(ask_version "$CURRENT")"
TAG="v$NEW"
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  echo "❌ Tag $TAG gibt es schon." >&2; exit 1
fi
echo "➡️  ${CURRENT} → ${NEW}"

# Version in Firmware und Server setzen
sed -i.bak -E "s/set\(PROJECT_VER \"[^\"]+\"\)/set(PROJECT_VER \"$NEW\")/" "$FW" && rm "$FW.bak"
sed -i.bak -E "s/^version = \"[^\"]+\"/version = \"$NEW\"/" "$PY" && rm "$PY.bak"
(cd server && uv lock -q)

echo "🧪 Tests ..."
if ! (cd server && uv run -q pytest -q >/dev/null 2>&1); then
  echo "❌ Tests rot – Version wird zurückgesetzt." >&2
  git checkout -- "$FW" "$PY" server/uv.lock
  exit 1
fi

git add "$FW" "$PY" server/uv.lock
git commit -qm "Version $NEW"
git switch -q main
git merge -q --ff-only origin/main
git merge --no-ff -q devel -m "Merge devel: Release $NEW"
git tag -a "$TAG" -m "$TAG"
git switch -q devel

echo "main: $(git log --oneline -1 main)"
echo "Tag:  $TAG"

if [[ "$DRY" == "--dry" ]]; then
  echo "--dry: nichts gepusht. Rückgängig: git tag -d $TAG; git switch main; git reset --hard origin/main; git switch devel; git reset --hard origin/devel"
  exit 0
fi

git push -q origin devel main "$TAG"
echo "✅ Version $NEW gepusht. GitHub baut jetzt Docker-Image und Release:"
echo "   https://github.com/ManuelW77/avinox-ridedata-server/actions"

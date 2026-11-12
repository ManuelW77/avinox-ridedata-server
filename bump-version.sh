#!/usr/bin/env bash
# Neue Version veröffentlichen, getrennt für Server und ESP-Firmware.
# Komponenten: server (server/pyproject.toml, Tag server-vX.Y.Z) und fw (esp32/avinox-sync/CMakeLists.txt, Tag fw-vX.Y.Z).
# Welche Komponente Änderungen hat, wird per git diff gegen den letzten Tag der Komponente erkannt
# (Fallback: letzter alter Tag vX.Y.Z). Gebumpt werden nur die erkannten bzw. erzwungenen Komponenten.
# Ablauf: Version setzen, (bei server) testen, committen, devel -> main mergen, Tags setzen, pushen.
# Tag server-v* startet Test + Docker-Image + Release, Tag fw-v* startet Firmware-Build + Release.
#
#   ./bump-version.sh                 # mit Abfrage (braucht ein Terminal), erkannte Komponenten
#   ./bump-version.sh minor           # ohne Versionsabfrage: patch | minor | major | X.Y.Z
#   ./bump-version.sh minor fw        # Komponente erzwingen: server | fw | both
#   ./bump-version.sh minor --dry     # alles lokal, nichts pushen
#   ./bump-version.sh --check         # nur Änderungserkennung ausgeben, nichts ändern
# Ohne Terminal (z. B. "!" in Claude Code) muss die Versionsauswahl als Argument kommen,
# es gibt dann keine Bestätigung der Komponenten (erkannte werden genommen).
set -euo pipefail

DRY="" CHOICE="" FORCE="" CHECK=""
for a in "$@"; do
  case "$a" in
    --dry) DRY="--dry" ;;
    --check) CHECK=1 ;;
    patch|minor|major) CHOICE="$a" ;;
    server|fw|both) FORCE="$a" ;;
    [0-9]*.[0-9]*.[0-9]*) CHOICE="$a" ;;
    *) echo "❌ Unbekanntes Argument: $a (erlaubt: patch|minor|major|X.Y.Z|server|fw|both|--dry|--check)" >&2; exit 1 ;;
  esac
done
FW=esp32/avinox-sync/CMakeLists.txt
PY=server/pyproject.toml

# Letzter Tag einer Komponente (Präfix server|fw), Fallback: letzter alter Tag vX.Y.Z.
# Die Tags liegen auf Merge-Commits von main und sind von devel aus nicht erreichbar, daher origin/main.
last_tag() {
  git describe --tags --abbrev=0 --match "$1-v*" origin/main 2>/dev/null \
    || git describe --tags --abbrev=0 --match 'v[0-9]*' origin/main 2>/dev/null || true
}

# Setzt <KOMP>_TAG, <KOMP>_N (geänderte Dateien) und <KOMP>_CHANGED (1/0)
detect() {
  local comp="$1" path="$2" label="$3" tag n
  tag="$(last_tag "$comp")"
  if [[ -z "$tag" ]]; then
    n="$(git ls-tree -r --name-only devel -- "$path" | wc -l | tr -d ' ')"
    echo "$label: kein Tag gefunden, $n Dateien" >&2
    eval "${comp}_TAG='' ${comp}_CHANGED=1"
    return
  fi
  n="$(git diff --name-only "$tag" devel -- "$path" | wc -l | tr -d ' ')"
  if [[ "$n" -gt 0 ]]; then
    echo "$label: $n Dateien geändert seit $tag" >&2
    eval "${comp}_TAG='$tag' ${comp}_CHANGED=1"
  else
    echo "$label: keine Änderungen seit $tag" >&2
    eval "${comp}_TAG='$tag' ${comp}_CHANGED=0"
  fi
}

ask_version() {
  local current="$1" choice="${2:-}" major minor patch custom
  if [[ ! "$current" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "❌ Unlesbare Version: $current" >&2; return 1
  fi
  IFS='.' read -r major minor patch <<< "$current"
  if [[ "$choice" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then echo "$choice"; return 0; fi
  if [[ -z "$choice" ]]; then
  if ! { exec 3</dev/tty; } 2>/dev/null; then
    echo "❌ Kein Terminal für die Abfrage. Auswahl als Argument angeben: ./bump-version.sh patch|minor|major|X.Y.Z" >&2
    return 1
  fi
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
  read -r -p "> " choice <&3 || { echo "❌ Keine Eingabe – abgebrochen." >&2; return 1; }
  choice="${choice:-1}"
  fi
  case "$choice" in
    1|patch) echo "${major}.${minor}.$((patch + 1))" ;;
    2|minor) echo "${major}.$((minor + 1)).0" ;;
    3|major) echo "$((major + 1)).0.0" ;;
    4)
      read -r -p "Neue Version (X.Y.Z): " custom <&3 || { echo "❌ Keine Eingabe – abgebrochen." >&2; return 1; }
      [[ "$custom" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "❌ Ungültiges Format, erwartet X.Y.Z" >&2; return 1; }
      echo "$custom" ;;
    5|q|abbrechen) echo "Abgebrochen." >&2; return 1 ;;
    *) echo "❌ Ungültige Auswahl: $choice" >&2; return 1 ;;
  esac
}

cd "$(git rev-parse --show-toplevel)"

if [[ -z "$CHECK" ]]; then
  [[ -z "$(git status --porcelain)" ]] || { echo "❌ Uncommittete Änderungen – erst committen." >&2; exit 1; }
fi

echo "🔄 Hole Stand von GitHub ..."
if [[ -n "$CHECK" ]]; then
  git fetch -q origin --tags || echo "⚠️  Fetch fehlgeschlagen – nutze lokalen Stand." >&2
else
  git fetch -q origin --tags
  git switch -q devel
  git merge -q --ff-only origin/devel || { echo "❌ devel weicht von origin/devel ab – erst abgleichen." >&2; exit 1; }
  if [[ -n "$(git log --oneline --no-merges devel..origin/main)" ]]; then
    echo "❌ origin/main hat Commits, die devel fehlen – erst nach devel mergen." >&2; exit 1
  fi
fi

server_TAG="" server_CHANGED=0 fw_TAG="" fw_CHANGED=0
detect server server/ "Server"
detect fw esp32/avinox-sync/ "Firmware"
if [[ -n "$CHECK" ]]; then
  echo "--check: nichts geändert." >&2; exit 0
fi

DO_SERVER=$server_CHANGED DO_FW=$fw_CHANGED
case "$FORCE" in
  server) DO_SERVER=1 DO_FW=0 ;;
  fw) DO_SERVER=0 DO_FW=1 ;;
  both) DO_SERVER=1 DO_FW=1 ;;
esac
if [[ "$DO_SERVER" == 0 && "$DO_FW" == 0 ]]; then
  echo "❌ Keine Änderungen in Server oder Firmware. Erzwingen mit: ./bump-version.sh server|fw|both" >&2; exit 1
fi

# Mit Terminal: Komponenten bestätigen (Default = erkannt)
if [[ -z "$FORCE" ]] && { exec 4</dev/tty; } 2>/dev/null; then
  sel="server"; [[ "$DO_SERVER" == 1 && "$DO_FW" == 1 ]] && sel="both"
  [[ "$DO_SERVER" == 0 ]] && sel="fw"
  read -r -p "Komponenten bumpen? server | fw | both [${sel}]: " ans <&4 || ans=""
  ans="${ans:-$sel}"
  case "$ans" in
    server) DO_SERVER=1 DO_FW=0 ;;
    fw) DO_SERVER=0 DO_FW=1 ;;
    both) DO_SERVER=1 DO_FW=1 ;;
    *) echo "Abgebrochen." >&2; exit 1 ;;
  esac
fi


TAGS=() MSG=() FILES=()
if [[ "$DO_SERVER" == 1 ]]; then
  CUR_S="$(sed -nE 's/^version = "([^"]+)".*/\1/p' "$PY" | head -1)"
  NEW_S="$(ask_version "$CUR_S" "$CHOICE")" || exit 1
  TAG_S="server-v$NEW_S"
  git rev-parse -q --verify "refs/tags/$TAG_S" >/dev/null && { echo "❌ Tag $TAG_S gibt es schon." >&2; exit 1; }
  echo "➡️  Server ${CUR_S} → ${NEW_S}"
  TAGS+=("$TAG_S"); MSG+=("server $NEW_S"); FILES+=("$PY" server/uv.lock)
fi
if [[ "$DO_FW" == 1 ]]; then
  CUR_F="$(sed -nE 's/.*set\(PROJECT_VER "([^"]+)"\).*/\1/p' "$FW")"
  NEW_F="$(ask_version "$CUR_F" "$CHOICE")" || exit 1
  TAG_F="fw-v$NEW_F"
  git rev-parse -q --verify "refs/tags/$TAG_F" >/dev/null && { echo "❌ Tag $TAG_F gibt es schon." >&2; exit 1; }
  echo "➡️  Firmware ${CUR_F} → ${NEW_F}"
  TAGS+=("$TAG_F"); MSG+=("fw $NEW_F"); FILES+=("$FW")
fi

if [[ "$DO_SERVER" == 1 ]]; then
  sed -i.bak -E "s/^version = \"[^\"]+\"/version = \"$NEW_S\"/" "$PY" && rm "$PY.bak"
  (cd server && uv lock -q)
fi
if [[ "$DO_FW" == 1 ]]; then
  sed -i.bak -E "s/set\(PROJECT_VER \"[^\"]+\"\)/set(PROJECT_VER \"$NEW_F\")/" "$FW" && rm "$FW.bak"
fi

if [[ "$DO_SERVER" == 1 ]]; then
  echo "🧪 Tests ..."
  if ! (cd server && uv run -q pytest -q >/dev/null 2>&1); then
    echo "❌ Tests rot – Version wird zurückgesetzt." >&2
    git checkout -- "${FILES[@]}"
    exit 1
  fi
fi

SUMMARY="$(IFS=' / '; echo "${MSG[*]}")"
git add "${FILES[@]}"
git commit -qm "Version $SUMMARY"
git switch -q main
git merge -q --ff-only origin/main
git merge --no-ff -q devel -m "Merge devel: Release $SUMMARY"
for t in "${TAGS[@]}"; do git tag -a "$t" -m "$t"; done
git switch -q devel

echo "main: $(git log --oneline -1 main)"
echo "Tags: ${TAGS[*]}"

if [[ "$DRY" == "--dry" ]]; then
  echo "--dry: nichts gepusht. Rückgängig: git tag -d ${TAGS[*]}; git switch main; git reset --hard origin/main; git switch devel; git reset --hard origin/devel"
  exit 0
fi

git push -q origin devel main "${TAGS[@]}"
echo "✅ ${SUMMARY} gepusht. GitHub baut jetzt:"
echo "   https://github.com/ManuelW77/avinox-ridedata-server/actions"

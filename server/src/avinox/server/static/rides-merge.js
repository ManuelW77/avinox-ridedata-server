/* Fahrtenliste: Fahrten auswählen und zusammenführen, Ein-Klick-Vorschläge. */
"use strict";
(() => {
  const list = document.getElementById("rides-list");
  if (!list) return;
  let I = {};
  try { I = JSON.parse(document.getElementById("i18n").textContent) || {}; } catch (e) { I = {}; }
  const tr = (key, vars) => {
    let s = I[key] != null ? I[key] : key;
    Object.keys(vars || {}).forEach((k) => { s = s.split("{" + k + "}").join(vars[k]); });
    return s;
  };
  const toast = (msg) => { if (window.avinoxToast) window.avinoxToast(msg, "error"); };

  async function merge(ids, buttons) {
    buttons.forEach((b) => { b.disabled = true; });
    try {
      const res = await fetch("/api/v1/rides/merge", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids }),
      });
      if (res.status === 409) { toast(tr("merge.no_start")); buttons.forEach((b) => { b.disabled = false; }); return; }
      if (!res.ok) throw new Error(res.status);
      const data = await res.json();
      window.location.href = "/rides/" + data.id;
    } catch (e) {
      buttons.forEach((b) => { b.disabled = false; });
      toast(tr("merge.failed"));
    }
  }

  // Vorschläge: ein Klick führt die beiden Fahrten zusammen
  list.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-merge]");
    if (!b) return;
    merge(b.dataset.merge.split(",").map(Number), [b]);
  });

  // Auswahlmodus
  const toggle = document.getElementById("rt-select");
  const bar = document.getElementById("merge-bar");
  const count = document.getElementById("merge-count");
  const go = document.getElementById("merge-go");
  const cancel = document.getElementById("merge-cancel");
  if (!toggle || !bar) return;
  const boxes = () => Array.from(list.querySelectorAll("input.merge-sel"));
  const picked = () => boxes().filter((c) => c.checked);

  const del = document.getElementById("merge-delete");
  const dlg = document.getElementById("rides-delete-dialog");

  function refresh() {
    const n = picked().length;
    if (del) del.disabled = n < 1;
    count.textContent = n >= 2 ? tr("merge.count", { n }) : (n ? tr("merge.count", { n }) + " · " : "") + tr("merge.count_hint");
    go.disabled = n < 2;
  }
  function setMode(on) {
    list.classList.toggle("selecting", on);
    bar.hidden = !on;
    toggle.setAttribute("aria-pressed", String(on));
    boxes().forEach((c) => { c.hidden = !on; if (!on) c.checked = false; });
    refresh();
  }
  toggle.addEventListener("click", () => setMode(!list.classList.contains("selecting")));
  cancel.addEventListener("click", () => setMode(false));
  list.addEventListener("change", (e) => { if (e.target.matches("input.merge-sel")) refresh(); });
  go.addEventListener("click", () => {
    const ids = picked().map((c) => Number(c.closest("li").dataset.id));
    if (ids.length >= 2) merge(ids, [go, cancel]);
  });

  // Löschen: Rückfrage im Dialog, dann alle ausgewählten Fahrten entfernen
  if (del && dlg && typeof dlg.showModal === "function") {
    dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close("cancel"); });
    del.addEventListener("click", () => {
      const sel = picked();
      if (!sel.length) return;
      const merged = sel.some((c) => Number(c.closest("li").dataset.merged) > 0);
      document.getElementById("rides-delete-text").textContent =
        tr("rides.delete.text", { n: sel.length }) + (merged ? " " + tr("rides.delete.merged") : "");
      dlg.showModal();
    });
    dlg.addEventListener("close", async () => {
      if (dlg.returnValue !== "delete") return;
      const ids = picked().map((c) => Number(c.closest("li").dataset.id));
      [go, del, cancel].forEach((b) => { b.disabled = true; });
      try {
        const res = await fetch("/api/v1/rides/delete", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids }),
        });
        if (!res.ok) throw new Error(res.status);
        window.location.reload();
      } catch (e) {
        cancel.disabled = false;
        refresh();
        toast(tr("rides.delete.failed"));
      }
    });
  }
})();

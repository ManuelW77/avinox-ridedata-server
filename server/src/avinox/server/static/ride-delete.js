/* Fahrt löschen: Rückfrage im eigenen Dialog, per DELETE entfernen, zurück zur Fahrtenliste. */
"use strict";
(() => {
  const btn = document.getElementById("delete-btn");
  const dlg = document.getElementById("delete-dialog");
  if (!btn || !dlg || typeof dlg.showModal !== "function") return;
  let I = {};
  try { I = JSON.parse(document.getElementById("i18n").textContent) || {}; } catch (e) { I = {}; }
  const tr = (key) => (I[key] != null ? I[key] : key);

  btn.addEventListener("click", () => dlg.showModal());
  // Klick auf den abgedunkelten Hintergrund schließt den Dialog
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close("cancel"); });

  dlg.addEventListener("close", async () => {
    if (dlg.returnValue !== "delete") return;
    btn.disabled = true;
    try {
      const res = await fetch(`/api/v1/rides/${btn.dataset.ride}`, { method: "DELETE" });
      if (!res.ok) throw new Error(res.status);
      window.location.href = "/rides";
    } catch (e) {
      btn.disabled = false;
      if (window.avinoxToast) window.avinoxToast(tr("delete.failed"), "error");
    }
  });
})();

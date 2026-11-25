/* Zusammengeführte Fahrt wieder in die Einzelfahrten trennen. */
"use strict";
(() => {
  const btn = document.getElementById("split-btn");
  if (!btn) return;
  let I = {};
  try { I = JSON.parse(document.getElementById("i18n").textContent) || {}; } catch (e) { I = {}; }
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    try {
      const res = await fetch(`/api/v1/rides/${btn.dataset.ride}/split`, { method: "POST" });
      if (!res.ok) throw new Error(res.status);
      window.location.href = "/rides";
    } catch (e) {
      btn.disabled = false;
      if (window.avinoxToast) window.avinoxToast(I["split.failed"] != null ? I["split.failed"] : "split.failed", "error");
    }
  });
})();

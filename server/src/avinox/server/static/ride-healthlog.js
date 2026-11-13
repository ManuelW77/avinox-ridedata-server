/* Puls einer Fahrt sofort aus HealthLog holen, danach Seite neu laden. */
"use strict";
(() => {
  const btn = document.getElementById("hl-fetch");
  if (!btn) return;
  let I = {};
  try { I = JSON.parse(document.getElementById("i18n").textContent) || {}; } catch (e) { I = {}; }
  const tr = (key) => (I[key] != null ? I[key] : key);
  const toast = (text, kind) => { if (window.avinoxToast) window.avinoxToast(text, kind); };

  btn.addEventListener("click", async () => {
    btn.disabled = true;
    try {
      const res = await fetch(`/api/v1/rides/${btn.dataset.ride}/healthlog`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.status === "ok") { window.location.reload(); return; }
      if (res.ok && data.status === "none") toast(tr("hl_none"), "error");
      else if (res.ok) toast(tr("hl_failed"), "error");
      else toast(data.detail && data.detail.error === "unreachable" ? tr("hl_unreachable") : tr("hl_failed"), "error");
    } catch (e) {
      toast(tr("hl_failed"), "error");
    }
    btn.disabled = false;
  });
})();

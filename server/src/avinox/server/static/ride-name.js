/* Fahrt umbenennen: Titel wird zum Textfeld (Enter = speichern, Esc = abbrechen, leer = automatischer Name). */
"use strict";
(() => {
  const title = document.getElementById("ride-title");
  const btn = document.getElementById("rename-btn");
  const textEl = document.getElementById("ride-title-text");
  if (!title || !btn || !textEl) return;
  let I = {};
  try { I = JSON.parse(document.getElementById("i18n").textContent) || {}; } catch (e) { I = {}; }
  const tr = (key) => (I[key] != null ? I[key] : key);

  btn.addEventListener("click", () => {
    const original = title.innerHTML;
    const form = document.createElement("form");
    form.className = "rename-form";
    const input = document.createElement("input");
    input.type = "text";
    input.maxLength = 80;
    input.value = title.dataset.custom || textEl.textContent.trim();
    input.placeholder = tr("rename.placeholder");
    input.setAttribute("aria-label", tr("rename.label"));
    const save = document.createElement("button");
    save.type = "submit";
    save.className = "btn";
    save.textContent = tr("rename.save");
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "btn ghost";
    cancel.textContent = tr("rename.cancel");
    form.append(input, save, cancel);
    title.replaceChildren(form);
    input.focus();
    input.select();

    const restore = () => {
      title.innerHTML = original;
      const b = document.getElementById("rename-btn");
      if (b) b.addEventListener("click", () => btn.click());
    };
    cancel.addEventListener("click", restore);
    input.addEventListener("keydown", (ev) => { if (ev.key === "Escape") { ev.preventDefault(); restore(); } });
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const name = input.value.trim();
      save.disabled = true;
      try {
        const res = await fetch(`/api/v1/rides/${title.dataset.ride}/name`, {
          method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: name || null }),
        });
        if (!res.ok) throw new Error(res.status);
        const data = await res.json();
        restore();
        title.dataset.custom = data.name_custom || "";
        document.getElementById("ride-title-text").textContent = data.name;
        document.title = document.title.replace(/^.*?(?= · )/, data.name);
      } catch (e) {
        save.disabled = false;
        input.focus();
      }
    });
  });
})();

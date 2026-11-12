"use strict";
(() => {
  // ---- Eigene Modi benennen
  const form = document.getElementById("modes-form");
  if (form) {
    const status = document.getElementById("modes-status");
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const body = {};
      form.querySelectorAll("input[data-level]").forEach((el) => { body[el.dataset.level] = el.value.trim(); });
      status.textContent = "Speichere …";
      try {
        const res = await fetch("/api/v1/settings/modes", {
          method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
        });
        status.textContent = res.ok ? "Gespeichert." : `Fehler ${res.status}`;
      } catch (e) {
        status.textContent = "Fehler: " + e.message;
      }
    });
  }

  // ---- Upload
  const patEl = document.getElementById("upload-patterns");
  const patterns = patEl ? JSON.parse(patEl.textContent).map((p) => new RegExp(p)) : [];
  const statusEl = document.getElementById("upload-status");
  const doneEl = document.getElementById("upload-done");
  const zone = document.getElementById("dropzone");
  const input = document.getElementById("folder-input");

  // Führende Ordner bis zum ersten bekannten Unterordner abschneiden.
  function cleanPath(p) {
    const m = /(^|\/)(ride_rec|buried_rec|hms_rec)\//.exec(p);
    return m ? p.slice(m.index + m[1].length) : null;
  }
  const wanted = (path) => path !== null && patterns.some((re) => re.test(path));

  let busy = false;
  async function upload(items) {
    // items: [{file, path}] – bereits gefiltert, nichts anderes wird gelesen
    if (busy) return;
    busy = true;
    doneEl.hidden = true;
    const c = { done: 0, neu: 0, known: 0, err: 0 };
    const show = () => {
      statusEl.textContent = `${c.done} / ${items.length} · neu: ${c.neu} · bekannt: ${c.known} · Fehler: ${c.err}`;
    };
    show();
    if (!items.length) {
      statusEl.textContent = "Keine passenden Dateien gefunden (erwartet: Ordner mit ride_rec/, buried_rec/ oder hms_rec/).";
      busy = false;
      return;
    }
    for (const { file, path } of items) {
      try {
        const fd = new FormData();
        fd.append("files", file, path);
        const res = await fetch("/api/v1/files", { method: "POST", body: fd });
        if (!res.ok) throw new Error(res.status);
        const r = (await res.json()).results[0];
        if (!r || !r.ok) c.err++; else if (r.new) c.neu++; else c.known++;
      } catch (e) {
        c.err++;
      }
      c.done++;
      show();
    }
    doneEl.hidden = false;
    busy = false;
  }

  function fromFileList(list) {
    const out = [];
    for (const file of list) {
      const path = cleanPath(file.webkitRelativePath || file.name);
      if (wanted(path)) out.push({ file, path });
    }
    return out;
  }

  // Drag-and-drop: Verzeichnisbaum durchlaufen, nur passende Dateien anfassen.
  function readEntries(reader) {
    return new Promise((ok, fail) => reader.readEntries(ok, fail));
  }
  async function walk(entry, out) {
    if (entry.isFile) {
      const path = cleanPath(entry.fullPath.replace(/^\//, ""));
      if (wanted(path)) out.push({ file: await new Promise((ok, fail) => entry.file(ok, fail)), path });
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      let batch;
      while ((batch = await readEntries(reader)).length) {
        for (const e of batch) await walk(e, out);
      }
    }
  }

  if (input) input.addEventListener("change", () => { upload(fromFileList(input.files)); input.value = ""; });
  if (zone) {
    zone.addEventListener("dragover", (ev) => { ev.preventDefault(); zone.classList.add("over"); });
    zone.addEventListener("dragleave", () => zone.classList.remove("over"));
    zone.addEventListener("drop", async (ev) => {
      ev.preventDefault();
      zone.classList.remove("over");
      const entries = [...ev.dataTransfer.items].map((i) => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
      const out = [];
      statusEl.textContent = "Lese Ordner …";
      for (const e of entries) await walk(e, out);
      upload(out);
    });
  }

  // ---- Token
  const token = document.getElementById("token");
  const showBtn = document.getElementById("token-show");
  const copyBtn = document.getElementById("token-copy");
  if (token && showBtn) {
    showBtn.addEventListener("click", () => {
      const hidden = token.type === "password";
      token.type = hidden ? "text" : "password";
      showBtn.textContent = hidden ? "verbergen" : "anzeigen";
    });
  }
  if (token && copyBtn) {
    copyBtn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(token.value);
      } catch (e) {
        token.type = "text"; token.select(); document.execCommand("copy"); token.type = "password";
      }
      copyBtn.textContent = "kopiert";
      setTimeout(() => { copyBtn.textContent = "kopieren"; }, 1500);
    });
  }
})();

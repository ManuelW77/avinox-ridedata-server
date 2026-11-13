"use strict";
(() => {
  // Texte aus <script id="i18n"> (siehe base.html), Sprache in I.lang
  let I = {};
  try { I = JSON.parse(document.getElementById("i18n").textContent) || {}; } catch (e) { I = {}; }
  const tr = (key, vars) => String(I[key] != null ? I[key] : key).replace(/\{(\w+)\}/g, (m, k) => (vars && vars[k] != null ? vars[k] : m));
  const LOCALE = I.lang === "en" ? "en-US" : "de-DE";
  // ---- Sprache: bei Auswahl sofort abschicken
  const langSelect = document.getElementById("lang-select");
  if (langSelect && langSelect.form) {
    const langSave = document.getElementById("lang-save");
    if (langSave) langSave.hidden = true;
    langSelect.addEventListener("change", () => {
      try { sessionStorage.setItem("avinox-toast", "lang"); } catch (e) {}
      langSelect.form.submit();
    });
  }
  const toast = (text, kind) => { if (window.avinoxToast) window.avinoxToast(text, kind); };
  // ---- Eigene Modi benennen
  const form = document.getElementById("modes-form");
  if (form) {
    const status = document.getElementById("modes-status");
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const body = {};
      form.querySelectorAll("input[data-level]").forEach((el) => { body[el.dataset.level] = el.value.trim(); });
      status.textContent = tr("saving");
      try {
        const res = await fetch("/api/v1/settings/modes", {
          method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
        });
        status.textContent = res.ok ? tr("saved") : tr("error_n", { n: res.status });
        if (res.ok) toast(tr("autosaved")); else toast(tr("error_n", { n: res.status }), "error");
      } catch (e) {
        status.textContent = tr("error_msg", { m: e.message });
        toast(tr("error_msg", { m: e.message }), "error");
      }
    });
  }

  // ---- HealthLog
  const hlForm = document.getElementById("hl-form");
  if (hlForm) {
    const $ = (id) => document.getElementById(id);
    const jsonHdr = { "Content-Type": "application/json" };
    const showHl = (d) => {
      $("hl-url").value = d.url || $("hl-url").value;
      $("hl-enabled").checked = !!d.enabled;
      $("hl-status").textContent = d.connected ? tr("hl_connected", { u: d.username }) : tr("hl_not_connected");
      $("hl-counts").textContent = d.connected ? "· " + tr("hl_counts", { ok: d.ok, open: d.open }) : "";
      $("hl-form").hidden = !!d.connected;
      $("hl-disconnect").hidden = !d.connected;
      $("hl-retry").hidden = !d.connected;
    };
    const HL_ERR = { invalid_url: "hl_err.invalid_url", unreachable: "hl_err.unreachable", bad_response: "hl_err.bad_response", invalid_credentials: "hl_err.invalid_credentials", mfa_invalid: "hl_err.mfa_invalid" };
    const errText = (res, data) => { const code = data.detail && data.detail.error; return HL_ERR[code] ? tr(HL_ERR[code]) : tr("error_n", { n: res.status }); };
    const hlMsg = (text) => { $("hl-msg").textContent = text || ""; $("hl-msg").hidden = !text; };
    fetch("/api/v1/settings/healthlog").then((r) => r.json()).then(showHl).catch(() => {});
    const putHl = async (body) => {
      const res = await fetch("/api/v1/settings/healthlog", { method: "PUT", headers: jsonHdr, body: JSON.stringify(body) });
      const data = await res.json().catch(() => ({}));
      if (res.ok) { showHl(data); toast(tr("autosaved")); return true; }
      toast(errText(res, data), "error");
      return false;
    };
    $("hl-enabled").addEventListener("change", async () => { if (!(await putHl({ enabled: $("hl-enabled").checked }))) $("hl-enabled").checked = !$("hl-enabled").checked; });
    $("hl-url").addEventListener("change", () => { if ($("hl-url").value.trim()) putHl({ url: $("hl-url").value.trim() }); });
    hlForm.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      hlMsg("");
      try {
        const res = await fetch("/api/v1/settings/healthlog/connect", {
          method: "POST", headers: jsonHdr,
          body: JSON.stringify({ url: $("hl-url").value.trim(), username: $("hl-user").value.trim(), password: $("hl-pass").value, mfa_code: $("hl-code").value.trim() || null }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          hlMsg(errText(res, data));
          return;
        }
        if (data.mfa_required) { $("hl-code-row").hidden = false; $("hl-code").focus(); hlMsg(tr("hl_need_code")); return; }
        $("hl-pass").value = ""; $("hl-code").value = ""; $("hl-code-row").hidden = true;
        showHl(data); toast(tr("hl_connected_toast"));
      } catch (e) { hlMsg(tr("error_msg", { m: e.message })); }
    });
    $("hl-retry").addEventListener("click", async () => {
      const res = await fetch("/api/v1/settings/healthlog/retry", { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { toast(errText(res, data), "error"); return; }
      showHl(data);
      const m = $("hl-retry-msg"); m.textContent = tr("hl_retry_done", { n: data.retried }); m.hidden = false;
    });
    $("hl-disconnect").addEventListener("click", async () => {
      const res = await fetch("/api/v1/settings/healthlog/disconnect", { method: "POST" });
      if (res.ok) { showHl(await res.json()); toast(tr("hl_disconnected_toast")); }
    });
  }

  // ---- Ortsnamen an/aus
  const geoBox = document.getElementById("geo-enabled");
  if (geoBox) {
    const geoStatus = document.getElementById("geo-status");
    const geoOff = document.getElementById("geo-off");
    const showGeo = (d) => {
      geoBox.checked = !!d.enabled;
      geoBox.disabled = !d.available;
      geoOff.hidden = !!d.available;
      geoStatus.textContent = d.available && d.enabled && d.pending > 0 ? tr("geo_pending", { n: d.pending }) : "";
    };
    fetch("/api/v1/settings/geocode").then((r) => r.json()).then(showGeo).catch(() => {});
    geoBox.addEventListener("change", async () => {
      try {
        const res = await fetch("/api/v1/settings/geocode", {
          method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: geoBox.checked }),
        });
        if (res.ok) { showGeo(await res.json()); toast(tr("autosaved")); }
        else { geoBox.checked = !geoBox.checked; toast(tr("error_n", { n: res.status }), "error"); }
      } catch (e) {
        geoBox.checked = !geoBox.checked;
        geoStatus.textContent = tr("error_msg", { m: e.message });
        toast(tr("error_msg", { m: e.message }), "error");
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
      statusEl.textContent = tr("upload_progress", { done: c.done, total: items.length, new: c.neu, known: c.known, err: c.err });
    };
    show();
    if (!items.length) {
      statusEl.textContent = tr("upload_none");
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
      statusEl.textContent = tr("reading_folder");
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
      showBtn.textContent = hidden ? tr("hide") : tr("show");
    });
  }
  if (token && copyBtn) {
    copyBtn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(token.value);
      } catch (e) {
        token.type = "text"; token.select(); document.execCommand("copy"); token.type = "password";
      }
      copyBtn.textContent = tr("copied");
      setTimeout(() => { copyBtn.textContent = tr("copy"); }, 1500);
    });
  }

  // ---- Server-URL kopieren
  document.querySelectorAll(".server-url-copy").forEach((urlCopy) => {
    const urlEl = urlCopy.parentElement.querySelector(".server-url");
    urlCopy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(urlEl.value);
      } catch (e) {
        urlEl.select(); document.execCommand("copy");
      }
      urlCopy.textContent = tr("copied");
      setTimeout(() => { urlCopy.textContent = tr("copy"); }, 1500);
    });
  });

  // ---- Firmware
  const fwForm = document.getElementById("fw-form");
  const fwStatus = document.getElementById("fw-status");
  if (fwForm) {
    fwForm.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const file = document.getElementById("fw-file").files[0];
      if (!file) return;
      const fd = new FormData();
      fd.append("file", file, file.name);
      fwStatus.textContent = tr("fw_uploading");
      try {
        const res = await fetch("/api/v1/firmware", { method: "POST", body: fd });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) { fwStatus.textContent = body.detail || tr("error_n", { n: res.status }); return; }
        location.reload();
      } catch (e) {
        fwStatus.textContent = tr("error_msg", { m: e.message });
      }
    });
  }
  document.querySelectorAll("[data-fw-current],[data-fw-delete]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const del = btn.hasAttribute("data-fw-delete");
      if (del && !confirm(tr("fw_confirm_delete"))) return;
      const id = del ? btn.dataset.fwDelete : btn.dataset.fwCurrent;
      const res = await fetch(`/api/v1/firmware/${id}` + (del ? "" : "/current"), { method: del ? "DELETE" : "POST" });
      if (res.ok) location.reload(); else if (fwStatus) fwStatus.textContent = tr("error_n", { n: res.status });
    });
  });

  // ---- Anmeldung: Passwort ändern, Benutzer verwalten (nur Admins sehen die Benutzerliste)
  const jsonHeaders = { "Content-Type": "application/json" };
  const authErr = (res, data) => { const code = data && data.detail && data.detail.error; const key = "auth_err." + code; return I[key] ? tr(key) : tr("error_n", { n: res.status }); };
  const pwForm = document.getElementById("pw-form");
  if (pwForm) {
    pwForm.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const res = await fetch("/api/v1/users/me/password", { method: "PUT", headers: jsonHeaders, body: JSON.stringify({ old_password: document.getElementById("pw-old").value, new_password: document.getElementById("pw-new").value }) });
      const data = await res.json().catch(() => ({}));
      if (res.ok) { pwForm.reset(); toast(tr("pw_saved")); } else toast(authErr(res, data), "error");
    });
  }
  const setupForm = document.getElementById("setup-form");
  if (setupForm) {
    setupForm.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const pass = document.getElementById("su-pass").value;
      if (pass !== document.getElementById("su-pass2").value) { toast(tr("setup_mismatch"), "error"); return; }
      const res = await fetch("/api/v1/setup/admin", { method: "POST", headers: { ...jsonHeaders, "X-Requested-With": "fetch" }, body: JSON.stringify({ username: document.getElementById("su-name").value.trim(), password: pass }) });
      const data = await res.json().catch(() => ({}));
      if (res.ok) location.reload(); else toast(authErr(res, data), "error");
    });
  }
  const userForm = document.getElementById("user-add-form");
  if (userForm) {
    userForm.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const res = await fetch("/api/v1/users", { method: "POST", headers: jsonHeaders, body: JSON.stringify({ username: document.getElementById("nu-name").value.trim(), password: document.getElementById("nu-pass").value, is_admin: document.getElementById("nu-admin").checked }) });
      const data = await res.json().catch(() => ({}));
      if (res.ok) location.reload(); else toast(authErr(res, data), "error");
    });
  }
  document.querySelectorAll("[data-user-delete]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!confirm(tr("user_confirm_delete", { u: btn.dataset.name }))) return;
      const res = await fetch(`/api/v1/users/${btn.dataset.userDelete}`, { method: "DELETE" });
      const data = await res.json().catch(() => ({}));
      if (res.ok) location.reload(); else toast(authErr(res, data), "error");
    });
  });
  document.querySelectorAll("[data-user-reset]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const password = prompt(tr("user_reset_prompt", { u: btn.dataset.name }));
      if (!password) return;
      const res = await fetch(`/api/v1/users/${btn.dataset.userReset}/password`, { method: "PUT", headers: jsonHeaders, body: JSON.stringify({ password }) });
      const data = await res.json().catch(() => ({}));
      if (res.ok) toast(tr("user_pw_reset")); else toast(authErr(res, data), "error");
    });
  });
})();

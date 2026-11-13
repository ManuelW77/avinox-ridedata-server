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

  // ---- MCP (Claude, ChatGPT)
  const mcpOn = document.getElementById("mcp-enabled");
  if (mcpOn) {
    const $ = (id) => document.getElementById(id);
    const jsonHdr = { "Content-Type": "application/json" };
    const showMcp = (d) => {
      mcpOn.checked = !!d.enabled;
      $("mcp-body").hidden = !(d.enabled && d.token);
      $("mcp-url").value = d.url_with_token || "";
      $("mcp-token").value = d.token || "";
      $("mcp-cmd").value = d.token ? `claude mcp add --transport http avinox ${d.url} --header "Authorization: Bearer ${d.token}"` : "";
    };
    const call = async (method, path, body) => {
      try {
        const res = await fetch("/api/v1/settings/mcp" + path, { method, headers: jsonHdr, body: body ? JSON.stringify(body) : undefined });
        const data = await res.json().catch(() => ({}));
        if (res.ok) { showMcp(data); return true; }
        toast(tr("error_n", { n: res.status }), "error");
      } catch (e) { toast(tr("error_msg", { m: e.message }), "error"); }
      return false;
    };
    fetch("/api/v1/settings/mcp").then((r) => (r.ok ? r.json() : null)).then((d) => { if (d) showMcp(d); }).catch(() => {});
    mcpOn.addEventListener("change", async () => {
      const want = mcpOn.checked;
      if (await call("PUT", "", { enabled: want })) toast(tr(want ? "mcp_on_toast" : "mcp_off_toast"));
      else mcpOn.checked = !want;
    });
    $("mcp-show").addEventListener("click", () => {
      const hidden = $("mcp-token").type === "password";
      $("mcp-token").type = hidden ? "text" : "password";
      $("mcp-show").textContent = tr(hidden ? "mcp_hide" : "mcp_show");
    });
    $("mcp-new").addEventListener("click", async () => {
      if (window.confirm(tr("mcp_new_confirm")) && (await call("POST", "/token"))) toast(tr("mcp_new_toast"));
    });
    document.querySelectorAll("[data-mcp-copy]").forEach((btn) => btn.addEventListener("click", async () => {
      const input = $(btn.dataset.mcpCopy);
      const value = input.value;
      try {
        await navigator.clipboard.writeText(value);
        toast(tr("mcp_copied"));
      } catch (e) {
        // Fallback (kein HTTPS o. Ä.): Feld markieren und kopieren
        const wasPw = input.type === "password";
        if (wasPw) input.type = "text";
        input.focus(); input.select();
        let ok = false;
        try { ok = document.execCommand("copy"); } catch (e2) { ok = false; }
        if (wasPw) input.type = "password";
        toast(tr(ok ? "mcp_copied" : "mcp_copy_fail"), ok ? undefined : "error");
      }
    }));
  }

  // ---- KI-Auswertung
  const aiSave = document.getElementById("ai-save");
  if (aiSave) {
    const $ = (id) => document.getElementById(id);
    const jsonHdr = { "Content-Type": "application/json" };
    let presets = [];
    const msg = (text) => { $("ai-msg").textContent = text || ""; $("ai-msg").hidden = !text; };
    const errMsg = (data, res) => (data && (data.message || (data.detail && (data.detail.message || data.detail)))) || tr("error_n", { n: res.status });
    const showAi = (d) => {
      presets = d.presets || presets;
      const sel = $("ai-provider");
      if (sel.options.length !== presets.length) {
        sel.textContent = "";
        presets.forEach((p) => { const o = document.createElement("option"); o.value = p.id; o.textContent = p.name; sel.appendChild(o); });
      }
      $("ai-enabled").checked = !!d.enabled;
      sel.value = d.provider || "";
      $("ai-url").value = d.base_url || "";
      $("ai-model").value = d.model || "";
      $("ai-lang").value = d.lang === "en" ? "en" : "de";
      $("ai-key").value = "";
      $("ai-key").placeholder = d.has_key ? (d.key_hint || "…") : "";
      $("ai-key-clear").hidden = !d.has_key;
      const c = d.counts || {};
      $("ai-status").textContent = d.busy ? tr("ai_busy") : tr("ai_counts", { done: c.done || 0, pending: c.pending || 0, errors: c.errors || 0 });
      $("ai-counts").textContent = d.last_error ? "· " + tr("ai_last_error", { m: d.last_error }) : "";
    };
    fetch("/api/v1/settings/ai").then((r) => r.json()).then(showAi).catch(() => {});
    $("ai-provider").addEventListener("change", () => {
      const p = presets.find((x) => x.id === $("ai-provider").value);
      if (p) { $("ai-url").value = p.base_url || ""; $("ai-model").value = p.model || ""; }
    });
    const put = async (body, okText) => {
      msg("");
      try {
        const res = await fetch("/api/v1/settings/ai", { method: "PUT", headers: jsonHdr, body: JSON.stringify(body) });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { toast(errMsg(data, res), "error"); return false; }
        showAi(data); toast(okText || tr("autosaved")); return true;
      } catch (e) { toast(tr("error_msg", { m: e.message }), "error"); return false; }
    };
    $("ai-enabled").addEventListener("change", async () => { if (!(await put({ enabled: $("ai-enabled").checked }))) $("ai-enabled").checked = !$("ai-enabled").checked; });
    const formBody = () => {
      const b = { provider: $("ai-provider").value, base_url: $("ai-url").value.trim(), model: $("ai-model").value.trim(), lang: $("ai-lang").value, enabled: $("ai-enabled").checked };
      if ($("ai-key").value) b.api_key = $("ai-key").value;
      return b;
    };
    aiSave.addEventListener("click", () => put(formBody(), tr("saved")));
    $("ai-key-clear").addEventListener("click", () => put({ api_key: "" }, tr("ai_key_removed")));
    $("ai-test").addEventListener("click", async () => {
      msg("");
      if (!(await put(formBody(), tr("saved")))) return;
      $("ai-test").disabled = true;
      try {
        const res = await fetch("/api/v1/settings/ai/test", { method: "POST" });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.ok) { msg(tr("ai_test_ok", { model: data.model || "", reply: data.reply || "" })); toast(tr("ai_test_toast")); }
        else { msg(tr("ai_test_fail", { m: errMsg(data, res) })); toast(tr("ai_test_toast_fail"), "error"); }
      } catch (e) { msg(tr("ai_test_fail", { m: e.message })); }
      finally { $("ai-test").disabled = false; }
    });
    $("ai-fill").addEventListener("click", async () => {
      msg("");
      try {
        const res = await fetch("/api/v1/settings/ai/fill", { method: "POST" });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { toast(errMsg(data, res), "error"); return; }
        showAi(data); msg(tr("ai_queued", { n: data.queued || 0 }));
      } catch (e) { toast(tr("error_msg", { m: e.message }), "error"); }
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
  const enableForm = document.getElementById("enable-form");
  if (enableForm) {
    enableForm.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const pass = document.getElementById("su-pass").value;
      const rep = document.getElementById("su-pass2");
      if (rep && pass !== rep.value) { toast(tr("setup_mismatch"), "error"); return; }
      const res = await fetch("/api/v1/auth/enable", { method: "POST", headers: { ...jsonHeaders, "X-Requested-With": "fetch" }, body: JSON.stringify({ username: document.getElementById("su-name").value.trim(), password: pass }) });
      const data = await res.json().catch(() => ({}));
      if (res.ok) location.reload(); else toast(authErr(res, data), "error");
    });
  }
  const disableBtn = document.getElementById("auth-disable");
  if (disableBtn) {
    disableBtn.addEventListener("click", async () => {
      if (!confirm(tr("disable_confirm"))) return;
      const res = await fetch("/api/v1/auth/disable", { method: "POST", headers: { "X-Requested-With": "fetch" } });
      const data = await res.json().catch(() => ({}));
      if (res.ok) location.reload(); else toast(authErr(res, data), "error");
    });
  }
  const oidcForm = document.getElementById("oidc-form");
  if (oidcForm) {
    const oidcStatus = document.getElementById("oidc-status");
    const val = (id) => document.getElementById(id).value.trim();
    oidcForm.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      if (oidcForm.dataset.locked) return;
      const res = await fetch("/api/v1/settings/oidc", { method: "PUT", headers: { ...jsonHeaders, "X-Requested-With": "fetch" }, body: JSON.stringify({
        enabled: document.getElementById("oi-on").checked, issuer: val("oi-issuer"), client_id: val("oi-client"), secret: document.getElementById("oi-secret").value,
        name: val("oi-name"), allowed: val("oi-allowed"), admin: val("oi-admin") }) });
      const data = await res.json().catch(() => ({}));
      if (res.ok) { document.getElementById("oi-secret").value = ""; document.getElementById("oi-secret").placeholder = data.has_secret ? tr("oidc_secret_set") : ""; toast(tr("saved")); } else toast(authErr(res, data), "error");
    });
    document.getElementById("oidc-test").addEventListener("click", async () => {
      oidcStatus.textContent = tr("oidc_testing");
      try {
        const res = await fetch("/api/v1/settings/oidc/test", { method: "POST", headers: { "X-Requested-With": "fetch" } });
        const data = await res.json().catch(() => ({}));
        const key = "oidc_test." + (data.ok ? "ok" : data.error);
        oidcStatus.textContent = res.ok ? (I[key] ? tr(key) : tr("oidc_test.failed")) : authErr(res, data);
      } catch (e) { oidcStatus.textContent = tr("oidc_test.unreachable"); }
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

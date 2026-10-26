/* Scripts de la pestaña Sistema (credenciales, usuarios del panel, notificaciones Telegram).
   Estaban inline en templates/tabs/updates.html; van en un fichero para poder usar una CSP con script-src 'self'.
   Solo se ejecutan si existe la pestaña Sistema (no en el login). */
if (document.getElementById("tab-updates")) {

(function () {
  const form = document.getElementById("security-auth-form");
  if (!form) return;
  const currentEl = document.getElementById("sec-auth-current");
  const userEl    = document.getElementById("sec-auth-user");
  const passEl    = document.getElementById("sec-auth-pass");
  const pass2El   = document.getElementById("sec-auth-pass2");
  const statusEl  = document.getElementById("sec-auth-status");
  const saveBtn   = document.getElementById("sec-auth-save");

  function setStatus(text, kind) {
    if (!statusEl) return;
    statusEl.classList.remove("ok", "err", "warn");
    if (kind) statusEl.classList.add(kind);
    statusEl.textContent = text || "";
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    setStatus("");
    const current_password = currentEl ? currentEl.value : "";
    const new_username     = userEl    ? (userEl.value.trim() || userEl.defaultValue) : "";
    const new_password     = passEl    ? passEl.value    : "";
    const new_password2    = pass2El   ? pass2El.value   : "";
    if (!current_password || !new_password || !new_password2) {
      setStatus("Rellena todos los campos requeridos.", "warn"); return;
    }
    if (new_password !== new_password2) {
      setStatus("Las nuevas contraseñas no coinciden.", "err"); return;
    }
    if (!confirm("¿Guardar nuevas credenciales? Se cerrará la sesión.")) return;
    if (saveBtn) saveBtn.disabled = true;
    setStatus("Guardando...");
    fetchJSON("/api/auth/update", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current_password, new_username, new_password }),
    })
      .then((data) => {
        if (data && data.error) { setStatus(String(data.error), "err"); return; }
        setStatus("Guardado. Cerrando sesión...", "ok");
        window.location.href = "/logout";
      })
      .catch((err) => setStatus(String(err || "Error"), "err"))
      .finally(() => { if (saveBtn) saveBtn.disabled = false; });
  });
})();

(function () {
  const listEl = document.getElementById("pu-list");
  const form = document.getElementById("pu-form");
  if (!listEl || !form) return;
  const statusEl = document.getElementById("pu-status");
  const userEl = document.getElementById("pu-user");
  const passEl = document.getElementById("pu-pass");
  const addBtn = document.getElementById("pu-add");
  const newBtn = document.getElementById("pu-new");
  const ROLES = ["viewer", "operator", "admin"];

  function setStatus(text, kind) {
    statusEl.classList.remove("ok", "err", "warn");
    if (kind) statusEl.classList.add(kind);
    statusEl.textContent = text || "";
  }

  function errText(e) {
    const m = String((e && e.message) || e || "Error");
    const i = m.indexOf("{");
    if (i >= 0) {
      try { const j = JSON.parse(m.slice(i)); if (j && j.error) return String(j.error); } catch (_) {}
    }
    return m;
  }

  function post(url, body) {
    return fetchJSON(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
  }

  function render(data) {
    listEl.textContent = "";
    const me = data && data.me;
    ((data && data.users) || []).forEach((u) => {
      const isMe = u.user === me;
      const row = document.createElement("div");
      row.className = "pu-row";

      const name = document.createElement("span");
      name.className = "pu-name";
      name.textContent = u.user + (isMe ? " (tú)" : "");

      const sel = document.createElement("div");
      sel.className = "pu-roles";
      ROLES.forEach((r) => {
        const lab = document.createElement("label");
        lab.className = "pu-radio";
        const inp = document.createElement("input");
        inp.type = "radio";
        inp.name = "pu-role-" + u.user;
        inp.value = r;
        inp.checked = r === u.role;
        inp.disabled = isMe;
        inp.addEventListener("change", () => {
          setStatus("Guardando...");
          post("/api/panel-users/" + encodeURIComponent(u.user) + "/role", { role: r })
            .then(() => { setStatus("Rol actualizado.", "ok"); load(); })
            .catch((e) => { setStatus(errText(e), "err"); load(); });
        });
        lab.appendChild(inp);
        lab.appendChild(document.createTextNode(" " + r));
        sel.appendChild(lab);
      });

      const del = document.createElement("button");
      del.type = "button";
      del.className = "btn";
      del.textContent = "Borrar";
      del.disabled = isMe;
      del.addEventListener("click", () => {
        if (!confirm("¿Borrar el usuario " + u.user + "?")) return;
        post("/api/panel-users/" + encodeURIComponent(u.user) + "/delete")
          .then(() => { setStatus("Usuario borrado.", "ok"); load(); })
          .catch((e) => { setStatus(errText(e), "err"); load(); });
      });

      row.appendChild(name);
      row.appendChild(sel);
      row.appendChild(del);
      listEl.appendChild(row);
    });
  }

  function load() {
    fetchJSON("/api/panel-users").then(render).catch((e) => setStatus(errText(e), "err"));
  }

  function showForm(show) {
    form.classList.toggle("hidden", !show);
    newBtn.setAttribute("aria-expanded", show ? "true" : "false");
    if (show) userEl.focus();
  }
  newBtn.addEventListener("click", () => showForm(form.classList.contains("hidden")));

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    setStatus("");
    addBtn.disabled = true;
    post("/api/panel-users", { user: userEl.value, password: passEl.value, role: (form.querySelector('input[name="pu-role"]:checked') || {}).value })
      .then(() => {
        setStatus("Usuario creado.", "ok");
        userEl.value = "";
        passEl.value = "";
        showForm(false);
        load();
      })
      .catch((err) => setStatus(errText(err), "err"))
      .finally(() => { addBtn.disabled = false; });
  });

  // core.js (fetchJSON) se carga despues de este bloque: esperar a que la pagina termine de cargar
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", load);
  else load();
})();

(function() {
  if (!document.getElementById('notif-save')) return;  // la seccion Telegram solo se muestra a admin
  function setNotifStatus(msg, type) {
    var el = document.getElementById('notif-status');
    if (!el) return;
    el.classList.remove("ok", "err", "warn");
    if (type) el.classList.add(type);
    el.textContent = msg;
  }

  function getNotifData() {
    var events = {};
    ['backup_ok','backup_err','backup_stale','nas_start','service_down','disk_full','smart_fail','temp_high','ram_low','ip_change','login_ok','login_fail','wg_peer'].forEach(function(ev) {
      var el = document.getElementById('notif-ev-' + ev);
      events[ev] = el ? el.checked : true;
    });
    return {
      enabled: document.getElementById('notif-enabled').checked,
      token: document.getElementById('notif-token').value.trim(),
      chat_id: document.getElementById('notif-chat-id').value.trim(),
      events: events,
    };
  }

  function loadNotifConfig() {
    fetch('/api/notifications')
      .then(function(r) { return r.json(); })
      .then(function(cfg) {
        document.getElementById('notif-enabled').checked = !!cfg.enabled;
        document.getElementById('notif-token').value = cfg.token || '';
        document.getElementById('notif-chat-id').value = cfg.chat_id || '';
        var evs = cfg.events || {};
        ['backup_ok','backup_err','backup_stale','nas_start','service_down','disk_full','smart_fail','temp_high','ram_low','ip_change','login_ok','login_fail','wg_peer'].forEach(function(ev) {
          var el = document.getElementById('notif-ev-' + ev);
          if (el) el.checked = evs[ev] !== false;
        });
      })
      .catch(function() {});
  }

  document.getElementById('notif-save').addEventListener('click', function() {
    fetch('/api/notifications', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(getNotifData()) })
      .then(function(r) { return r.json(); })
      .then(function(res) { setNotifStatus(res.ok ? 'Guardado' : (res.error || 'Error'), res.ok ? 'ok' : 'err'); })
      .catch(function(e) { setNotifStatus(String(e), 'err'); });
  });

  document.getElementById('notif-test').addEventListener('click', function() {
    setNotifStatus('Guardando y enviando...', '');
    fetch('/api/notifications', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(getNotifData()) })
      .then(function() {
        return fetch('/api/notifications/test', { method: 'POST' });
      })
      .then(function(r) { return r.json(); })
      .then(function(res) { setNotifStatus(res.ok ? 'Mensaje enviado' : (res.error || 'Error'), res.ok ? 'ok' : 'err'); })
      .catch(function(e) { setNotifStatus(String(e), 'err'); });
  });

  var tabBtn = document.querySelector('[data-tab="updates"]');
  if (tabBtn) tabBtn.addEventListener('click', loadNotifConfig);
  if (document.querySelector('[data-tab="updates"].active')) loadNotifConfig();
})();

(function () {
  const section = document.getElementById("kernel-section");
  if (!section) return;  // solo se muestra a admin
  const $ = (id) => document.getElementById(id);
  const form = $("kn-form"), statusEl = $("kn-status"), logEl = $("kn-log"), progEl = $("kn-progress");
  const activateBtn = $("kn-activate"), discardBtn = $("kn-discard"), uploadBtn = $("kn-upload");
  let pollTimer = null, newVersion = null, busy = false;

  function setStatus(text, kind) {
    statusEl.classList.remove("ok", "err", "warn");
    if (kind) statusEl.classList.add(kind);
    statusEl.textContent = text || "";
  }
  function mb(n) { return (n / 1048576).toFixed(2) + " MB"; }
  function describe(f) {
    if (!f) return "—";
    return (f.name || "sin cabecera") + " · " + mb(f.size) + " · " + new Date(f.mtime * 1000).toLocaleString("es-ES");
  }
  function errText(e) { return (e && e.message) ? e.message : String(e); }
  function post(url, body) {
    return fetchJSON(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  }

  function renderJob(job) {
    const lines = (job && job.log) || [];
    logEl.textContent = lines.join("\n");
    logEl.classList.toggle("hidden", lines.length === 0);
    if (job && job.running) setStatus("Preparando: " + (job.step || "..."), "warn");
    else if (job && job.ok === true) setStatus("Preparado. Ahora prueba el arranque a mano desde U-Boot con /uImage.new y /uInitrd.new.", "ok");
    else if (job && job.ok === false) setStatus("Fallo: " + (job.error || "error desconocido"), "err");
  }

  function render(st) {
    const f = st.files || {};
    $("kn-running").textContent = st.running_kernel || "—";
    $("kn-boot").textContent = describe(f["uImage"]) + " / " + describe(f["uInitrd"]);
    $("kn-old").textContent = describe(f["uImage.old"]) + " / " + describe(f["uInitrd.old"]);
    $("kn-new").textContent = st.new_ready ? describe(f["uImage.new"]) + " / " + describe(f["uInitrd.new"]) : "—";
    const nm = f["uImage.new"] && f["uImage.new"].name;
    newVersion = st.new_ready && nm && nm.indexOf("Linux-") === 0 ? nm.slice(6) : null;
    const running = !!(st.job && st.job.running);
    activateBtn.disabled = busy || running || !newVersion;
    discardBtn.disabled = busy || running || !(st.new_ready || (st.uploaded || []).length || ((st.job && st.job.log) || []).length);
    uploadBtn.disabled = busy || running;
    renderJob(st.job);
    clearTimeout(pollTimer);
    if (running) pollTimer = setTimeout(load, 3000);
  }

  function load() {
    fetchJSON("/api/kernel/status").then(render).catch((e) => setStatus(errText(e), "err"));
  }

  function startPrepare() {
    return post("/api/kernel/prepare").then((res) => {
      if (res.error) { setStatus(res.error, "err"); return; }
      setStatus("Preparando...", "warn");
      load();
    });
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const file = $("kn-file").files[0];
    const sha = $("kn-sha").value.trim().toLowerCase();
    if (!file) { setStatus("Elige el paquete .tar.bz2.", "warn"); return; }
    if (!/^linux-\d+\.\d+\.\d+-kirkwood-tld-\d+-bodhi\.tar\.bz2$/.test(file.name)) {
      setStatus("El nombre debe ser como linux-7.2.11-kirkwood-tld-1-bodhi.tar.bz2", "err"); return;
    }
    if (!/^[0-9a-f]{64}$/.test(sha)) { setStatus("El sha256 debe tener 64 caracteres hexadecimales.", "err"); return; }
    if (!confirm("¿Subir " + file.name + " (" + mb(file.size) + ") y preparar el kernel? No toca los ficheros de arranque actuales.")) return;
    busy = true;
    uploadBtn.disabled = true;
    progEl.value = 0;
    progEl.classList.remove("hidden");
    setStatus("Subiendo...", "warn");
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/kernel/upload");
    xhr.setRequestHeader("X-Filename", file.name);
    xhr.setRequestHeader("X-Sha256", sha);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.upload.onprogress = (ev) => { if (ev.lengthComputable) progEl.value = Math.round(ev.loaded * 100 / ev.total); };
    xhr.onerror = () => { busy = false; progEl.classList.add("hidden"); setStatus("Error de red al subir.", "err"); load(); };
    xhr.onload = () => {
      busy = false;
      progEl.classList.add("hidden");
      if (xhr.status === 401) { window.location.href = "/login"; return; }
      let res = null;
      try { res = JSON.parse(xhr.responseText); } catch (err) { /* respuesta no JSON */ }
      if (xhr.status === 413) { setStatus("nginx rechaza el fichero por tamaño: falta subir client_max_body_size para /api/kernel/upload.", "err"); return; }
      if (!res || res.error || !res.ok) { setStatus((res && res.error) || ("Error HTTP " + xhr.status), "err"); load(); return; }
      setStatus("Subido y verificado (sha256 correcto).", "ok");
      startPrepare().catch((err) => setStatus(errText(err), "err"));
    };
    xhr.send(file);
  });

  activateBtn.addEventListener("click", () => {
    if (!newVersion) return;
    if (!confirm("¿Dejar fijo el kernel " + newVersion + "?\n\nEl kernel actual pasa a uImage.old / uInitrd.old (sustituye a la copia anterior) y el NAS arrancará con el nuevo en el próximo reinicio.\n\nHazlo solo si ya lo has probado a mano desde U-Boot.")) return;
    busy = true; activateBtn.disabled = true;
    setStatus("Activando...", "warn");
    post("/api/kernel/activate", { version: newVersion })
      .then((res) => {
        if (res.error) setStatus(res.error, "err");
        else setStatus("Activado " + res.version + ". Se usará en el próximo reinicio. Para volver atrás: uImage.old y uInitrd.old en /boot.", "ok");
      })
      .catch((err) => setStatus(errText(err), "err"))
      .finally(() => { busy = false; load(); });
  });

  discardBtn.addEventListener("click", () => {
    if (!confirm("¿Descartar lo preparado? Se borran uImage.new, uInitrd.new y el paquete subido. El kernel instalado no cambia.")) return;
    busy = true; discardBtn.disabled = true;
    post("/api/kernel/discard")
      .then((res) => setStatus(res.error || "Descartado.", res.error ? "err" : "ok"))
      .catch((err) => setStatus(errText(err), "err"))
      .finally(() => { busy = false; load(); });
  });

  section.addEventListener("toggle", () => { if (section.open) load(); });
})();

}

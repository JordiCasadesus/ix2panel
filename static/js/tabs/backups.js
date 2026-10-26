// =====================================================
// Backups tab - jobs UI
// =====================================================

const BROWSERS = {
  src: {
    inputId: "backup-src",
    browserId: "src-browser",
    shortcutsId: "src-shortcuts",
    dropdownId: "src-dropdown",
    pickBtnId: "pick-src",
    upBtnId: "src-up",
    refreshBtnId: "src-refresh",
    defaultPath: "/data1",
  },
  dst: {
    inputId: "backup-dst-input",
    browserId: "dst-browser",
    shortcutsId: "dst-shortcuts",
    dropdownId: "dst-dropdown",
    pickBtnId: "pick-dst",
    upBtnId: "dst-up",
    refreshBtnId: "dst-refresh",
    defaultPath: "/mnt",
  },
  nasDst: {
    inputId: "ssh-nas-dst-input",
    browserId: "nas-dst-browser",
    shortcutsId: "nas-dst-shortcuts",
    dropdownId: "nas-dst-dropdown",
    defaultPath: "/data1",
  },
  nasSrc: {
    inputId: "ssh-nas-src-input",
    browserId: "nas-src-browser",
    shortcutsId: "nas-src-shortcuts",
    dropdownId: "nas-src-dropdown",
    defaultPath: "/data1",
  },
  localSrc: {
    inputId: "local-src-input",
    browserId: "local-src-browser",
    shortcutsId: "local-src-shortcuts",
    dropdownId: "local-src-dropdown",
    defaultPath: "/",
  },
  localDst: {
    inputId: "local-dst-input",
    browserId: "local-dst-browser",
    shortcutsId: "local-dst-shortcuts",
    dropdownId: "local-dst-dropdown",
    defaultPath: "/data1",
  },
};

const browserState = {
  src: { currentPath: "/" },
  dst: { currentPath: "/" },
  nasDst: { currentPath: "/" },
  nasSrc: { currentPath: "/" },
  localSrc: { currentPath: "/" },
  localDst: { currentPath: "/" },
};

const backupUi = {
  data: null,
  selectedId: "",
  creating: false,
  editing: false,
  _snapshot: "",
};

// El editor va bloqueado (fieldset disabled) hasta pulsar "Editar": evita cambiar una IP o una ruta sin querer.
function setBackupEditMode(on) {
  backupUi.editing = !!on;
  const fs = document.getElementById("backup-fields");
  if (fs) fs.disabled = !on;
  const show = (id, v) => document.getElementById(id)?.classList.toggle("hidden", !v);
  const hasJob = backupUi.creating || !!currentJob();
  show("backup-edit", !on && hasJob && window.IX2_ROLE !== "viewer");
  show("backup-save", on);
  show("backup-cancel-edit", on);
  show("backup-delete", on && !backupUi.creating);
  show("backup-run-job", !on);
  show("backup-view-log", !on);
  backupUi._snapshot = on ? JSON.stringify(collectEditorData()) : "";
}

function _backupEditsDirty() {
  return backupUi.editing && JSON.stringify(collectEditorData()) !== backupUi._snapshot;
}

// true si se puede cambiar de backup: no hay edicion con cambios, o el usuario acepta descartarlos.
function _confirmDiscardEdits() {
  if (!_backupEditsDirty()) return true;
  return confirm("Hay cambios sin guardar en este backup. ¿Descartarlos?");
}

function cancelEditJob() {
  if (!_confirmDiscardEdits()) return;
  if (backupUi.creating) {
    backupUi.creating = false;
    const jobs = (backupUi.data && Array.isArray(backupUi.data.jobs)) ? backupUi.data.jobs : [];
    selectJob(backupUi.selectedId || (jobs[0] && jobs[0].id) || "", false);
    return;
  }
  fillEditor(currentJob());
  setBackupEditMode(false);
}

function apiJSON(url, opts) {
  if (typeof window.fetchJSON === "function") return window.fetchJSON(url, opts);
  return fetch(url, opts).then((r) => r.json());
}

function normalizePath(path) {
  const raw = String(path || "").trim();
  if (!raw) return "/";
  const single = raw.replace(/\/+/g, "/");
  return single === "" ? "/" : single;
}

function parentPath(path) {
  const p = normalizePath(path);
  if (p === "/") return "/";
  const parts = p.split("/").filter(Boolean);
  if (parts.length <= 1) return "/";
  return "/" + parts.slice(0, -1).join("/");
}

// ---- Árbol de directorios (usa PathTreeUI.createLazyBrowser) ----
const _browsers = {};

function _getOrCreateBrowser(kind) {
  if (_browsers[kind]) return _browsers[kind];
  const cfg = BROWSERS[kind];
  const box = document.getElementById(cfg.browserId);
  if (!box || !window.PathTreeUI?.createLazyBrowser) return null;
  if (window.IX2_ROLE === "viewer") return null;  // /api/fs/list exige operator: un viewer no explora carpetas
  const browser = window.PathTreeUI.createLazyBrowser({
    container: box,
    root: cfg.defaultPath || "/data1",
    onSelect: (path) => {
      browserState[kind].currentPath = path;
      const input = document.getElementById(cfg.inputId);
      if (input) input.value = path;
      document.getElementById(cfg.dropdownId)?.classList.add("hidden");
    },
  });
  _browsers[kind] = browser;
  return browser;
}

function _renderTree(kind) {
  const cfg = BROWSERS[kind];
  const shortcuts = document.getElementById(cfg.shortcutsId);
  if (shortcuts) shortcuts.innerHTML = "";
  _getOrCreateBrowser(kind);
}
// ---- Fin árbol ----

function setBrowserPath(kind, path, load = true) {
  const cfg = BROWSERS[kind];
  if (!cfg) return;
  const input = document.getElementById(cfg.inputId);
  const normalized = normalizePath(path);
  if (input) input.value = normalized;
  browserState[kind].currentPath = normalized;
  if (load && window.IX2_ROLE !== "viewer") {  // /api/fs/list exige operator: un viewer solo ve la ruta escrita
    if (_browsers[kind]) {
      _browsers[kind].setSelected(normalized);
    } else {
      _renderTree(kind);
    }
  }
}

function renderShortcuts(kind, mounts = []) {
  _renderTree(kind);
}

function initPathBrowser(kind) {
  const cfg = BROWSERS[kind];
  if (!cfg) return;
  const input = document.getElementById(cfg.inputId);
  if (!input) return;

  function openDropdown() {
    const dd = document.getElementById(cfg.dropdownId);
    if (!dd) return;
    dd.classList.remove("hidden");
    delete _browsers[kind];
    _renderTree(kind);
  }

  function closeDropdown() {
    const dd = document.getElementById(cfg.dropdownId);
    if (dd) dd.classList.add("hidden");
  }

  input.addEventListener("focus", openDropdown);
  input.addEventListener("change", () => setBrowserPath(kind, input.value || cfg.defaultPath, false));
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); closeDropdown(); input.blur(); }
    if (ev.key === "Escape") { closeDropdown(); input.blur(); }
  });

  document.addEventListener("click", (e) => {
    const wrap = input.closest(".path-picker-wrap");
    if (wrap && !wrap.contains(e.target)) closeDropdown();
  }, true);
}

function _jobIsPending(job) {
  if (!job || job.enabled === false || job.running) return false;
  const sched = job.schedule || {};
  const schedTime = sched.time || "";
  if (!schedTime) return false;
  const freq = sched.frequency || "daily";
  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  const nowHHMM = pad(now.getHours()) + ":" + pad(now.getMinutes());
  if (nowHHMM < schedTime) return false;
  const last = job.last_backup || "";
  if (!last) return true;
  const lastDate = new Date(last.replace(" ", "T"));
  if (isNaN(lastDate.getTime())) return true;
  if (freq === "daily") {
    const toDay = (d) => d.getFullYear() + "-" + pad(d.getMonth()+1) + "-" + pad(d.getDate());
    return toDay(now) !== toDay(lastDate);
  }
  if (freq === "weekly") {
    const weekStart = (d) => { const s = new Date(d); s.setDate(d.getDate() - d.getDay()); return s.toDateString(); };
    return weekStart(now) !== weekStart(lastDate);
  }
  if (freq === "monthly") {
    return now.getFullYear() !== lastDate.getFullYear() || now.getMonth() !== lastDate.getMonth();
  }
  return false;
}

function updateBackupEnabledUi(enabled) {
  const runBtn = document.getElementById("backup-run-job");
  if (runBtn) runBtn.disabled = enabled === false;
}

function renderSummary(data) {}

function renderTasks(data) {
  const list = document.getElementById("backup-tasks");
  if (!list) return;
  list.innerHTML = "";
  const tasks = Array.isArray(data.tasks) ? data.tasks : [];
  if (!tasks.length) {
    list.innerHTML = '<div class="process-item">Sin tareas registradas</div>';
    return;
  }
  tasks.slice(0, 6).forEach((t) => {
    const detail = String(t.detail || "");
    const item = document.createElement("div");
    const state = /^ERROR/i.test(detail) ? "err" : (/^OK/i.test(detail) ? "ok" : "");
    const isLong = detail.length > 110;
    item.className = "process-item task" + (state ? " " + state : "") + (isLong ? " long" : "");
    const nameEl = document.createElement("span");
    nameEl.className = "process-name";
    nameEl.textContent = t.name || "-";
    const detEl = document.createElement("span");
    detEl.className = "task-detail";
    detEl.textContent = detail;
    detEl.title = isLong ? "Pulsa para ver el detalle completo" : detail;
    item.appendChild(nameEl);
    item.appendChild(detEl);
    if (isLong) {
      item.tabIndex = 0;
      item.setAttribute("role", "button");
      item.setAttribute("aria-expanded", "false");
      const toggle = () => {
        const open = item.classList.toggle("expanded");
        item.setAttribute("aria-expanded", open ? "true" : "false");
      };
      item.addEventListener("click", toggle);
      item.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); toggle(); }
      });
    }
    list.appendChild(item);
  });
}

function renderJobsList(data) {
  const list = document.getElementById("backup-jobs-list");
  if (!list) return;
  list.innerHTML = "";
  const jobs = Array.isArray(data.jobs) ? data.jobs : [];
  if (!jobs.length) {
    list.innerHTML = '<div class="process-item">No hay backups creados</div>';
    return;
  }
  jobs.forEach((job) => {
    const item = document.createElement("div");
    item.className = "process-item row-interactive" + (job.id === backupUi.selectedId ? " active" : "");
    let ledClass, stateLabel;
    if (job.running) {
      ledClass = "warn"; stateLabel = "En ejecución…";
    } else if (job.enabled === false) {
      ledClass = "warn"; stateLabel = "Desactivado";
    } else if (_jobIsPending(job)) {
      ledClass = "pending"; stateLabel = "Pendiente…";
    } else if (job.status === "ok") {
      ledClass = "ok"; stateLabel = job.last_backup || "sin ejecutar";
    } else if (job.status === "err") {
      ledClass = "err"; stateLabel = job.last_backup ? `Error - ${job.last_backup}` : "Error";
    } else {
      ledClass = "warn"; stateLabel = job.last_backup || "sin ejecutar";
    }
    const progressHtml = job.current_line
      ? `<span class="backup-current-line">${job.current_line.replace(/</g, "&lt;")}</span>`
      : "";
    const barHtml = job.running
      ? `<div class="backup-progress-bar"><div class="backup-progress-fill"></div></div>`
      : "";
    item.innerHTML = `
      <div class="backup-job-row">
        <span class="process-name">${escHtml(job.name || "Backup")}</span>
        ${progressHtml}
        <span class="process-stats"><span class="status ${ledClass}"></span> ${stateLabel}</span>
      </div>
      ${barHtml}
    `;
    item.addEventListener("click", () => {
      if (!_confirmDiscardEdits()) return;
      selectJob(job.id, true);
    });
    list.appendChild(item);
  });
}

function currentJob() {
  const jobs = (backupUi.data && Array.isArray(backupUi.data.jobs)) ? backupUi.data.jobs : [];
  return jobs.find((j) => j.id === backupUi.selectedId) || null;
}

function _parseExclusions(val) {
  return val || "";
}

function fillEditor(job) {
  document.getElementById("backup-name").value = job?.name || "";
  document.getElementById("backup-enabled").checked = job ? job.enabled !== false : true;
  document.getElementById("backup-frequency").value = job?.schedule?.frequency || "daily";
  document.getElementById("backup-time").value = job?.schedule?.time || "03:00";

  const mode = job?.mode || "rsync";
  const modeRadio = document.querySelector(`input[name="backup-mode"][value="${mode}"]`);
  if (modeRadio) modeRadio.checked = true;
  updateModeUi(mode);

  if (mode === "ssh") {
    const ssh = job?.ssh || {};

    // Origen
    const srcType = ssh.src_type || "pc";
    const srcRadio = document.querySelector(`input[name="ssh-src-type"][value="${srcType}"]`);
    if (srcRadio) srcRadio.checked = true;
    updateSshSrcUi(srcType);
    if (srcType === "nas") {
      setBrowserPath("nasSrc", job?.source || BROWSERS.nasSrc.defaultPath, true);
    } else {
      const pcSrc = document.getElementById("pc-src-input");
      if (pcSrc) pcSrc.value = job?.source || "";
    }

    // Destino
    const dstType = ssh.dst_type || "pc";
    const dstRadio = document.querySelector(`input[name="ssh-dst-type"][value="${dstType}"]`);
    if (dstRadio) dstRadio.checked = true;
    updateSshDstUi(dstType);
    if (dstType === "nas") {
      setBrowserPath("nasDst", job?.destination || BROWSERS.nasDst.defaultPath, true);
    } else {
      const sshDst = document.getElementById("ssh-dst-input");
      if (sshDst) sshDst.value = job?.destination || "";
    }

    // Config SSH
    const sshHost = document.getElementById("ssh-host");
    const sshPort = document.getElementById("ssh-port");
    const sshUser = document.getElementById("ssh-user");
    const sshKey  = document.getElementById("ssh-key");
    if (sshHost) sshHost.value = ssh.host || "";
    if (sshPort) sshPort.value = ssh.port || 22;
    if (sshUser) sshUser.value = ssh.user || "Administrator";
    if (sshKey)  sshKey.value  = ssh.key_path || "/root/.ssh/id_rsa";

    // Opciones robocopy
    const xdEl = document.getElementById("robocopy-xd");
    const xfEl = document.getElementById("robocopy-xf");
    if (xdEl) xdEl.value = _parseExclusions(ssh.xd || "");
    if (xfEl) xfEl.value = _parseExclusions(ssh.xf || "");

    const wgConf = document.getElementById("wg-conf");
    if (wgConf) wgConf.value = ssh.wg_conf || "";
    const wgDetails = document.getElementById("wg-details");
    if (wgDetails) wgDetails.open = !!(ssh.wg_conf || ssh.wg_conf_set);
    const wgGenBtn = document.getElementById('wg-gen-btn');
    if (wgGenBtn) wgGenBtn.textContent = (ssh.wg_conf || ssh.wg_conf_set) ? 'Config ya generada' : 'Generar config WG';

    const nasUser = document.getElementById("ssh-nas-user");
    const nasPass = document.getElementById("ssh-nas-pass");
    if (nasUser) nasUser.value = ssh.nas_user || "";
    if (nasPass) {
      nasPass.value = ssh.nas_pass || "";
      nasPass.placeholder = ssh.nas_pass_set ? "Guardada (vacío = conservarla)" : "";
    }
  } else if (mode === "local") {
    setBrowserPath("localSrc", job?.source || BROWSERS.localSrc.defaultPath, true);
    setBrowserPath("localDst", job?.destination || BROWSERS.localDst.defaultPath, true);
    const localExcludes = document.getElementById("local-excludes");
    if (localExcludes) localExcludes.value = job?.local?.excludes || "/proc\n/sys\n/dev\n/run\n/tmp";
  } else {
    setBrowserPath("src", job?.source || BROWSERS.src.defaultPath, true);
    setBrowserPath("dst", job?.destination || BROWSERS.dst.defaultPath, true);
    const smb = job?.smb || {};
    const smbHost = document.getElementById("smb-host");
    const smbShare = document.getElementById("smb-share");
    const smbUser = document.getElementById("smb-user");
    const smbPass = document.getElementById("smb-pass");
    const smbMount = document.getElementById("smb-mount");
    const smbVers = document.getElementById("smb-vers");
    if (smbHost) smbHost.value = smb.host || "";
    if (smbShare) smbShare.value = smb.share || "";
    if (smbUser) smbUser.value = smb.username || "";
    if (smbPass) {
      smbPass.value = smb.password || "";
      smbPass.placeholder = smb.password_set ? "Guardada (vacío = conservarla)" : "";
    }
    if (smbMount) smbMount.value = smb.mountpoint || "";
    if (smbVers) smbVers.value = smb.vers || "3.0";
    const rsyncExcl = document.getElementById("rsync-excludes");
    if (rsyncExcl) rsyncExcl.value = smb.excludes || "";
    const smbDetails = document.getElementById("smb-details");
    if (smbDetails) smbDetails.open = !!(smb.host || smb.mountpoint);
  }

  const subtitle = document.getElementById("backup-editor-subtitle");
  if (subtitle) subtitle.textContent = job ? `Editando: ${job.name || job.id}` : "Nuevo backup";
  updateBackupEnabledUi(job ? (job.enabled !== false) : true);
}

function collectEditorData() {
  const mode = document.querySelector('input[name="backup-mode"]:checked')?.value || "rsync";
  const isSsh = mode === "ssh";
  const sshSrcType = document.querySelector('input[name="ssh-src-type"]:checked')?.value || "pc";
  const sshDstType = document.querySelector('input[name="ssh-dst-type"]:checked')?.value || "pc";
  const payload = {
    id: backupUi.selectedId || "",
    name: document.getElementById("backup-name")?.value || "",
    enabled: document.getElementById("backup-enabled")?.checked !== false,
    mode,
    source: isSsh
      ? (sshSrcType === "nas"
          ? document.getElementById("ssh-nas-src-input")?.value || ""
          : document.getElementById("pc-src-input")?.value || "")
      : (mode === "local"
          ? document.getElementById("local-src-input")?.value || ""
          : document.getElementById("backup-src")?.value || ""),
    destination: isSsh
      ? (sshDstType === "nas"
          ? document.getElementById("ssh-nas-dst-input")?.value || ""
          : document.getElementById("ssh-dst-input")?.value || "")
      : (mode === "local"
          ? document.getElementById("local-dst-input")?.value || ""
          : document.getElementById("backup-dst-input")?.value || ""),
    schedule: {
      frequency: document.getElementById("backup-frequency")?.value || "daily",
      time: document.getElementById("backup-time")?.value || "03:00",
    },
  };
  if (isSsh) {
    payload.ssh = {
      host: document.getElementById("ssh-host")?.value || "",
      port: parseInt(document.getElementById("ssh-port")?.value || "22", 10),
      user: document.getElementById("ssh-user")?.value || "Administrator",
      key_path: document.getElementById("ssh-key")?.value || "/root/.ssh/id_rsa",
      src_type: sshSrcType,
      dst_type: sshDstType,
      xd: document.getElementById("robocopy-xd")?.value || "",
      xf: document.getElementById("robocopy-xf")?.value || "",
      wg_conf: document.getElementById("wg-conf")?.value || "",
      nas_user: document.getElementById("ssh-nas-user")?.value || "",
      nas_pass: document.getElementById("ssh-nas-pass")?.value || "",
    };
  } else if (mode === "local") {
    payload.local = {
      excludes: document.getElementById("local-excludes")?.value || "",
    };
  } else {
    payload.smb = {
      host: document.getElementById("smb-host")?.value || "",
      share: document.getElementById("smb-share")?.value || "",
      username: document.getElementById("smb-user")?.value || "",
      password: document.getElementById("smb-pass")?.value || "",
      mountpoint: document.getElementById("smb-mount")?.value || "",
      vers: document.getElementById("smb-vers")?.value || "3.0",
      excludes: document.getElementById("rsync-excludes")?.value || "",
    };
  }
  return payload;
}

function selectJob(jobId, persist) {
  backupUi.creating = false;
  backupUi.selectedId = jobId || "";
  renderJobsList(backupUi.data || { jobs: [] });
  fillEditor(currentJob());
  setBackupEditMode(false);
  if (persist && jobId) {
    apiJSON("/api/backups/select", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: jobId }),
    }).catch(() => {});
  }
}

function newJob() {
  if (!_confirmDiscardEdits()) return;
  backupUi.creating = true;
  backupUi.selectedId = "";
  renderJobsList(backupUi.data || { jobs: [] });
  fillEditor(null);
  setBackupEditMode(true);
}

function saveJob() {
  const payload = collectEditorData();
  const isUpdate = !backupUi.creating && !!payload.id;
  const url = isUpdate ? "/api/backups/jobs/update" : "/api/backups/jobs/create";
  apiJSON(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
    .then((res) => {
      if (res.error) {
        alert(res.error);
        return;
      }
      const savedId = (res.job && res.job.id) || payload.id;
      backupUi.creating = false;
      if (savedId) backupUi.selectedId = savedId;
      backupUi._loaded = false;
      loadBackupsData();
    })
    .catch((err) => alert(String(err)));
}

function deleteJob() {
  const job = currentJob();
  if (!job) {
    alert("Selecciona un backup para eliminar.");
    return;
  }
  if (!confirm(`Eliminar backup "${job.name}"?`)) return;
  apiJSON("/api/backups/jobs/delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: job.id }),
  })
    .then((res) => {
      if (res.error) {
        alert(res.error);
        return;
      }
      backupUi._loaded = false;
      loadBackupsData();
    })
    .catch((err) => alert(String(err)));
}

let _pollInterval = null;
let _progressInterval = null;
let _progressJobId = null;

// ---- PC browser (SSH mode) ----
const pcBrowser = { currentPath: "", dirs: [], loading: false, isRoot: true };

function updateModeUi(mode) {
  const isSsh = mode === "ssh";
  const isLocal = mode === "local";
  const isRsync = !isSsh && !isLocal;
  const show = (id, vis) => {
    const el = document.getElementById(id);
    if (el) el.style.display = vis ? "" : "none";
  };
  show("src-section-rsync", isRsync);
  show("src-section-ssh", isSsh);
  show("src-section-local", isLocal);
  show("dst-section-rsync", isRsync);
  show("dst-section-ssh", isSsh);
  show("dst-section-local", isLocal);
  show("ssh-config-section", isSsh);
  show("robocopy-section", isSsh);
  show("wg-section", isSsh);
  show("smb-section", isRsync);
  show("rsync-excl-section", isRsync);
}

function updateSshSrcUi(type) {
  const isNas = type === "nas";
  const pcWrap = document.getElementById("ssh-src-pc-wrap");
  const nasWrap = document.getElementById("ssh-src-nas-wrap");
  if (pcWrap) pcWrap.style.display = isNas ? "none" : "";
  if (nasWrap) nasWrap.style.display = isNas ? "" : "none";
}

function updateSshDstUi(type) {
  const isNas = type === "nas";
  const pcWrap = document.getElementById("ssh-dst-pc-wrap");
  const nasWrap = document.getElementById("ssh-dst-nas-wrap");
  if (pcWrap) pcWrap.style.display = isNas ? "none" : "";
  if (nasWrap) nasWrap.style.display = isNas ? "" : "none";
}

function renderPcBrowser() {
  const box = document.getElementById("pc-browser");
  if (!box) return;
  box.innerHTML = "";

  if (pcBrowser.loading) {
    const hint = document.createElement("div");
    hint.className = "tree-row tree-hint";
    hint.textContent = "Cargando...";
    box.appendChild(hint);
    return;
  }

  // Up row — oculto en raíz de unidades
  if (!pcBrowser.isRoot) {
    const upRow = document.createElement("div");
    upRow.className = "tree-row";
    upRow.innerHTML = '<span class="tree-arr">⬆</span><span class="tree-lbl">..</span>';
    upRow.addEventListener("click", () => {
      const p = pcBrowser.currentPath.replace(/[\\/]+$/, "");
      const sep = p.includes("\\") ? "\\" : "/";
      const parts = p.split(sep).filter(Boolean);
      // Si solo queda la letra de unidad (ej. ["C:"]) → volver a raíz de unidades
      if (parts.length <= 1) { loadPcDir(""); return; }
      const parent = parts.slice(0, -1).join(sep) + sep;
      loadPcDir(parent);
    });
    box.appendChild(upRow);
  }

  if (!pcBrowser.dirs.length) {
    const hint = document.createElement("div");
    hint.className = "tree-row tree-hint";
    hint.textContent = pcBrowser.isRoot ? "(sin unidades)" : "(sin subcarpetas)";
    box.appendChild(hint);
    return;
  }

  pcBrowser.dirs.forEach((d) => {
    // En raíz, d ya es un path completo (ej. "C:\"); en subdirectorio, lo concatenamos
    const childPath = pcBrowser.isRoot ? d : (
      (pcBrowser.currentPath.endsWith("\\") || pcBrowser.currentPath.endsWith("/")
        ? pcBrowser.currentPath : pcBrowser.currentPath + "\\") + d + "\\"
    );
    const icon = pcBrowser.isRoot ? "💾" : "📁";
    const row = document.createElement("div");
    row.className = "tree-row";
    row.innerHTML = `<span class="tree-arr">▸</span><span class="tree-lbl">${icon} ${escHtml(d)}</span>`;
    row.addEventListener("click", () => loadPcDir(childPath));
    box.appendChild(row);
  });
}

function loadPcDir(path) {
  const jobId = backupUi.selectedId;
  if (!jobId) {
    const box = document.getElementById("pc-browser");
    if (box) box.innerHTML = `<div class="tree-row tree-hint" style="color:#f88">Guarda el job primero (con host SSH configurado) para explorar el PC.</div>`;
    return;
  }
  pcBrowser.loading = true;
  renderPcBrowser();
  const url = `/api/backups/pc/browse?job_id=${encodeURIComponent(jobId)}&path=${encodeURIComponent(path)}`;
  apiJSON(url)
    .then((data) => {
      pcBrowser.loading = false;
      if (data.error) {
        const box = document.getElementById("pc-browser");
        if (box) box.innerHTML = `<div class="tree-row tree-hint" style="color:#f88">${escHtml(data.error)}</div>`;
        return;
      }
      pcBrowser.currentPath = data.path || path;
      pcBrowser.isRoot = !!data.is_root;
      pcBrowser.dirs = data.dirs || [];
      const input = document.getElementById("pc-src-input");
      if (input) input.value = pcBrowser.isRoot ? "" : pcBrowser.currentPath;
      renderPcBrowser();
    })
    .catch(() => { pcBrowser.loading = false; renderPcBrowser(); });
}

function initPcBrowser() {
  const input = document.getElementById("pc-src-input");
  if (!input) return;

  input.addEventListener("focus", () => {
    const dd = document.getElementById("pc-dropdown");
    if (dd) dd.classList.remove("hidden");
    // Si el campo está vacío, mostrar listado de unidades; si tiene valor, navegar a ese path
    loadPcDir(input.value || "");
  });
  input.addEventListener("change", () => {
    pcBrowser.currentPath = input.value || "";
  });
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); document.getElementById("pc-dropdown")?.classList.add("hidden"); input.blur(); }
    if (ev.key === "Escape") { document.getElementById("pc-dropdown")?.classList.add("hidden"); input.blur(); }
  });
  document.addEventListener("click", (e) => {
    const wrap = input.closest(".path-picker-wrap");
    if (wrap && !wrap.contains(e.target)) document.getElementById("pc-dropdown")?.classList.add("hidden");
  }, true);
}

// ---- PC browser — Destino ----
const pcDstBrowser = { currentPath: "", dirs: [], loading: false, isRoot: true };

function renderPcDstBrowser() {
  const box = document.getElementById("pc-dst-browser");
  if (!box) return;
  box.innerHTML = "";

  if (pcDstBrowser.loading) {
    box.innerHTML = '<div class="tree-row tree-hint">Cargando...</div>';
    return;
  }

  if (!pcDstBrowser.isRoot) {
    const upRow = document.createElement("div");
    upRow.className = "tree-row";
    upRow.innerHTML = '<span class="tree-arr">⬆</span><span class="tree-lbl">..</span>';
    upRow.addEventListener("click", () => {
      const p = pcDstBrowser.currentPath.replace(/[\\/]+$/, "");
      const sep = p.includes("\\") ? "\\" : "/";
      const parts = p.split(sep).filter(Boolean);
      if (parts.length <= 1) { loadPcDstDir(""); return; }
      loadPcDstDir(parts.slice(0, -1).join(sep) + sep);
    });
    box.appendChild(upRow);
  }

  if (!pcDstBrowser.dirs.length) {
    const hint = document.createElement("div");
    hint.className = "tree-row tree-hint";
    hint.textContent = pcDstBrowser.isRoot ? "(sin unidades)" : "(sin subcarpetas)";
    box.appendChild(hint);
    return;
  }

  pcDstBrowser.dirs.forEach((d) => {
    const childPath = pcDstBrowser.isRoot ? d : (
      (pcDstBrowser.currentPath.endsWith("\\") || pcDstBrowser.currentPath.endsWith("/")
        ? pcDstBrowser.currentPath : pcDstBrowser.currentPath + "\\") + d + "\\"
    );
    const icon = pcDstBrowser.isRoot ? "💾" : "📁";
    const row = document.createElement("div");
    row.className = "tree-row";
    row.innerHTML = `<span class="tree-arr">▸</span><span class="tree-lbl">${icon} ${escHtml(d)}</span>`;
    row.addEventListener("click", () => {
      loadPcDstDir(childPath);
      const input = document.getElementById("ssh-dst-input");
      if (input) input.value = childPath;
    });
    box.appendChild(row);
  });
}

function loadPcDstDir(path) {
  const jobId = backupUi.selectedId;
  if (!jobId) {
    const box = document.getElementById("pc-dst-browser");
    if (box) box.innerHTML = `<div class="tree-row tree-hint" style="color:#f88">Guarda el job primero para explorar el PC.</div>`;
    return;
  }
  pcDstBrowser.loading = true;
  renderPcDstBrowser();
  const url = `/api/backups/pc/browse?job_id=${encodeURIComponent(jobId)}&path=${encodeURIComponent(path)}`;
  apiJSON(url)
    .then((data) => {
      pcDstBrowser.loading = false;
      if (data.error) {
        const box = document.getElementById("pc-dst-browser");
        if (box) box.innerHTML = `<div class="tree-row tree-hint" style="color:#f88">${escHtml(data.error)}</div>`;
        return;
      }
      pcDstBrowser.currentPath = data.path || path;
      pcDstBrowser.isRoot = !!data.is_root;
      pcDstBrowser.dirs = data.dirs || [];
      renderPcDstBrowser();
    })
    .catch(() => { pcDstBrowser.loading = false; renderPcDstBrowser(); });
}

function initPcDstBrowser() {
  const input = document.getElementById("ssh-dst-input");
  if (!input) return;
  input.addEventListener("focus", () => {
    const dd = document.getElementById("pc-dst-dropdown");
    if (dd) dd.classList.remove("hidden");
    loadPcDstDir(input.value || "");
  });
  input.addEventListener("change", () => {
    pcDstBrowser.currentPath = input.value || "";
  });
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); document.getElementById("pc-dst-dropdown")?.classList.add("hidden"); input.blur(); }
    if (ev.key === "Escape") { document.getElementById("pc-dst-dropdown")?.classList.add("hidden"); input.blur(); }
  });
  document.addEventListener("click", (e) => {
    const wrap = input.closest(".path-picker-wrap");
    if (wrap && !wrap.contains(e.target)) document.getElementById("pc-dst-dropdown")?.classList.add("hidden");
  }, true);
}

function generateSshKey() {
  const keyPath = '/root/.ssh/id_rsa';
  const btn = document.getElementById('ssh-keygen-btn');
  const result = document.getElementById('ssh-keygen-result');
  const pre = document.getElementById('ssh-keygen-text');
  if (!btn || !result || !pre) return;

  btn.disabled = true;
  btn.textContent = 'Generando...';
  result.style.display = 'none';

  apiJSON('/api/backups/ssh-keygen', { method: 'POST', body: JSON.stringify({ key_path: keyPath }) })
    .then((res) => {
      result.style.display = 'block';
      if (res.error) {
        pre.textContent = 'Error: ' + res.error;
        btn.textContent = 'Reintentar';
      } else {
        pre.textContent = res.pubkey;
        btn.textContent = res.already_existed ? 'Clave ya existia' : 'Clave generada';
        // Rellenar  en cmd-win y abrir la seccion OpenSSH
        const cmdWin = document.getElementById('cmd-win');
        if (cmdWin) {
          cmdWin.textContent = cmdWin.textContent.replace(/PEGAR_AQUI_LA_CLAVE_PUB|ssh-ed25519 AAAA\S+/g, res.pubkey);
        }
        const details = document.getElementById('openssh-setup');
        if (details) details.open = true;
      }
    })
    .catch((e) => {
      result.style.display = 'block';
      pre.textContent = 'Error: ' + String(e);
      btn.textContent = 'Reintentar';
    })
    .finally(() => { btn.disabled = false; });
}

function initCopyCommands() {
  document.querySelectorAll(".openssh-cmd").forEach((pre) => {
    pre.addEventListener("click", () => {
      navigator.clipboard?.writeText(pre.textContent).then(() => {
        const orig = pre.title;
        pre.title = "Copiado!";
        setTimeout(() => { pre.title = orig; }, 1500);
      });
    });
  });
}

function _showProgress(jobId) {
  _progressJobId = jobId;
  const sec = document.getElementById("backup-progress-section");
  if (sec) sec.style.display = "block";
  let box = document.getElementById("backup-progress-box");
  if (!box) {
    const anchor = document.getElementById("backup-tasks");
    if (!anchor) return;
    box = document.createElement("div");
    box.id = "backup-progress-box";
    box.className = "backup-progress-box";
    box.innerHTML =
      '<div class="backup-progress-title">Progreso rsync</div>' +
      '<pre id="backup-progress-out" class="backup-progress-out"></pre>';
    anchor.parentNode.insertBefore(box, anchor.nextSibling);
  }
  box.style.display = "block";
  if (_progressInterval) clearInterval(_progressInterval);
  _progressInterval = setInterval(() => {
    fetch(`/api/backups/progress/${_progressJobId}`, { credentials: "same-origin" })
      .then((r) => r.json())
      .then((data) => {
        const out = document.getElementById("backup-progress-out");
        if (out && Array.isArray(data.lines)) {
          out.textContent = data.lines.slice(-30).join("\n");
          out.scrollTop = out.scrollHeight;
        }
        // Actualizar panel de progreso
        const sec = document.getElementById("backup-progress-section");
        if (sec) sec.style.display = data.running ? "block" : "none";
        if (data.running) {
          const fmtB = b => b == null ? "—" : b >= 1e9 ? (b/1e9).toFixed(1)+" GB" : b >= 1e6 ? (b/1e6).toFixed(1)+" MB" : (b/1e3).toFixed(0)+" KB";
          const fmtT = s => { if (!s) return "—"; const h=Math.floor(s/3600), m=Math.floor((s%3600)/60); return h ? h+"h "+m+"m" : m+"m "+Math.floor(s%60)+"s"; };
          const el = id => document.getElementById(id);
          if (el("bp-copied")) el("bp-copied").textContent = fmtB(data.bytes_copied);
          if (el("bp-total"))  el("bp-total").textContent  = fmtB(data.bytes_total);
          if (el("bp-speed"))  el("bp-speed").textContent  = data.speed_bps ? fmtB(data.speed_bps)+"/s" : "—";
          if (el("bp-eta"))    el("bp-eta").textContent    = fmtT(data.eta_sec);
          if (el("bp-elapsed"))el("bp-elapsed").textContent= fmtT(data.elapsed_sec);
          const bar = document.getElementById("backup-progress-bar");
          if (bar) bar.style.width = (data.percent || 0) + "%";
        }
        if (!data.running) {
          clearInterval(_progressInterval);
          _progressInterval = null;
        }
      })
      .catch(() => {});
  }, 2000);
}

function _hideProgress() {
  if (_progressInterval) { clearInterval(_progressInterval); _progressInterval = null; }
  const sec = document.getElementById("backup-progress-section");
  if (sec) sec.style.display = "none";
  const box = document.getElementById("backup-progress-box");
  if (box) box.style.display = "none";
}

function _startPolling() {
  if (_pollInterval) return;
  _pollInterval = setInterval(() => {
    loadBackupsData();
    const jobs = (backupUi.data && backupUi.data.jobs) || [];
    if (!jobs.some((j) => j.running)) {
      clearInterval(_pollInterval);
      _pollInterval = null;
    }
  }, 2000);
  // Seguridad: parar tras 2 horas
  setTimeout(() => { if (_pollInterval) { clearInterval(_pollInterval); _pollInterval = null; } }, 7200000);
}

function runSelectedJob() {
  const job = currentJob();
  if (!job) {
    alert("Selecciona un backup para ejecutar.");
    return;
  }
  apiJSON("/api/backups/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ job_id: job.id }),
  })
    .then((res) => {
      if (res.error) {
        alert(res.error);
        return;
      }
      loadBackupsData();
      if (res.running) _startPolling();
    })
    .catch((err) => alert(String(err)));
}

function viewLastLog() {
  const job = currentJob();
  if (!job) {
    alert("Selecciona un backup para ver el log.");
    return;
  }
  fetch(`/api/backups/log/${job.id}`, { credentials: "same-origin" })
    .then((r) => r.json())
    .then((res) => {
      if (res.error) {
        alert(res.error);
        return;
      }
      const win = window.open("", "_blank", "width=900,height=600,scrollbars=yes");
      win.document.write(
        `<html><head><title>Log: ${escHtml(job.name)}</title>` +
        `<style>body{background:#1a1a2e;color:#cdd6f4;font-family:monospace;font-size:13px;padding:16px;white-space:pre-wrap;word-break:break-all;}</style>` +
        `</head><body>${res.log.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")}</body></html>`
      );
      win.document.close();
    })
    .catch((err) => alert(String(err)));
}

function refreshMounts() {
  const list = document.getElementById("mounts-list");
  if (!list) return Promise.resolve([]);

  return apiJSON("/api/mounts")
    .then((data) => {
      if (data.error) {
        list.innerHTML = `<div class="process-item">Error: ${escHtml(data.error)}</div>`;
        renderShortcuts("src", []);
        renderShortcuts("dst", []);
        return [];
      }

      const items = Array.isArray(data.items) ? data.items : [];
      list.innerHTML = "";
      items.forEach((m) => {
        const item = document.createElement("div");
        item.className = "process-item row-interactive";
        item.innerHTML = `<span class="process-name">${escHtml(m.target)}</span><span class="process-stats">${escHtml(m.source)}</span>`;
        item.addEventListener("click", () => setBrowserPath("dst", m.target, true));
        list.appendChild(item);
      });

      renderShortcuts("src", items);
      renderShortcuts("dst", items);
      return items;
    })
    .catch(() => {
      renderShortcuts("src", []);
      renderShortcuts("dst", []);
      return [];
    });
}

function mountSmb() {
  const payload = {
    host: document.getElementById("smb-host")?.value || "",
    share: document.getElementById("smb-share")?.value || "",
    username: document.getElementById("smb-user")?.value || "",
    password: document.getElementById("smb-pass")?.value || "",
    mountpoint: document.getElementById("smb-mount")?.value || "",
    vers: document.getElementById("smb-vers")?.value || "3.0",
  };
  if (!payload.host || !payload.share || !payload.username || !payload.mountpoint) {
    alert("Completa host, share, usuario y montaje.");
    return;
  }

  apiJSON("/api/mounts/add", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
    .then((res) => {
      if (res.error) {
        alert(res.error);
      } else {
        refreshMounts().then(() => setBrowserPath("dst", payload.mountpoint || "/mnt", true));
      }
    });
}

function unmountSmb() {
  const mountpoint = document.getElementById("smb-mount")?.value || "";
  if (!mountpoint) {
    alert("Indica el punto de montaje.");
    return;
  }
  apiJSON("/api/mounts/remove", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mountpoint }),
  })
    .then((res) => {
      if (res.error) alert(res.error);
      else refreshMounts();
    });
}

function loadBackupsData() {
  apiJSON("/api/backups")
    .then((data) => {
      if (!data || data.error) return;
      backupUi.data = data;
      const jobs = Array.isArray(data.jobs) ? data.jobs : [];
      const isInitialLoad = !backupUi._loaded;
      backupUi._loaded = true;
      if (backupUi.creating) {
        if (isInitialLoad) fillEditor(null);
      } else if (backupUi.selectedId && jobs.some((j) => j.id === backupUi.selectedId)) {
        if (isInitialLoad) fillEditor(currentJob());
      } else {
        selectJob(data.current_job_id || (jobs[0] && jobs[0].id) || "", false);
      }
      if (isInitialLoad) setBackupEditMode(backupUi.creating);
      renderSummary(data);
      renderTasks(data);
      renderJobsList(data);
      if (jobs.some((j) => j.running)) _startPolling();
    })
    .catch((err) => {
      console.error("backups load error", err);
    });
}


function initWgGen() {
  const btn = document.getElementById('wg-gen-btn');
  if (!btn) return;
  btn.addEventListener('click', () => {
    const textarea = document.getElementById('wg-conf');
    const note = document.getElementById('wg-gen-note');
    const details = document.getElementById('wg-details');
    if (!textarea) return;

    // Si ya tiene config guardada, solo mostrarla
    if (textarea.value.trim()) {
      if (details) details.open = true;
      note.textContent = 'Config existente. Edita o copia.';
      note.style.display = 'block';
      btn.textContent = 'Config ya generada';
      return;
    }

    // Generar nuevo peer
    const jobName = document.getElementById('backup-name')?.value || 'backup-pc';
    btn.disabled = true;
    btn.textContent = 'Generando...';
    note.style.display = 'none';

    apiJSON('/api/wg/peer/add', { method: 'POST', body: JSON.stringify({ name: jobName }) })
      .then((res) => {
        if (res.error) {
          note.textContent = 'Error: ' + res.error;
          note.style.display = 'block';
          btn.textContent = 'Generar config WG';
        } else {
          textarea.value = res.client_conf;
          if (details) details.open = true;
          note.textContent = 'Config generada para ' + res.ip + '. Pulsa Guardar cambios para conservarla.';
          note.style.display = 'block';
          btn.textContent = 'Config generada';
        }
      })
      .catch((e) => {
        note.textContent = 'Error: ' + String(e);
        note.style.display = 'block';
        btn.textContent = 'Generar config WG';
      })
      .finally(() => { btn.disabled = false; });
  });
}

function initBackupsUi() {
  if (document.querySelector("form.login-form")) return;
  initPathBrowser("src");
  initPathBrowser("dst");
  initPathBrowser("nasDst");
  initPathBrowser("nasSrc");
  initPathBrowser("localSrc");
  initPathBrowser("localDst");

  setBrowserPath("src", BROWSERS.src.defaultPath, true);
  setBrowserPath("dst", BROWSERS.dst.defaultPath, true);

  const saveBtn = document.getElementById("backup-save");
  if (saveBtn) saveBtn.addEventListener("click", saveJob);

  const editBtn = document.getElementById("backup-edit");
  if (editBtn) editBtn.addEventListener("click", () => setBackupEditMode(true));

  const cancelEditBtn = document.getElementById("backup-cancel-edit");
  if (cancelEditBtn) cancelEditBtn.addEventListener("click", cancelEditJob);

  const newBtn = document.getElementById("backup-new");
  if (newBtn) newBtn.addEventListener("click", newJob);

  const deleteBtn = document.getElementById("backup-delete");
  if (deleteBtn) deleteBtn.addEventListener("click", deleteJob);

  const runBtn = document.getElementById("backup-run-job");
  if (runBtn) runBtn.addEventListener("click", runSelectedJob);

  const logBtn = document.getElementById("backup-view-log");
  if (logBtn) logBtn.addEventListener("click", viewLastLog);

  const enabledEl = document.getElementById("backup-enabled");
  if (enabledEl) enabledEl.addEventListener("change", () => updateBackupEnabledUi(enabledEl.checked));

  document.querySelectorAll('input[name="backup-mode"]').forEach((radio) => {
    radio.addEventListener("change", () => updateModeUi(radio.value));
  });

  document.querySelectorAll('input[name="ssh-src-type"]').forEach((radio) => {
    radio.addEventListener("change", () => updateSshSrcUi(radio.value));
  });

  document.querySelectorAll('input[name="ssh-dst-type"]').forEach((radio) => {
    radio.addEventListener("change", () => updateSshDstUi(radio.value));
  });


  const wgCopyBtn = document.getElementById("wg-copy-btn");
  if (wgCopyBtn) wgCopyBtn.addEventListener("click", () => {
    const txt = document.getElementById("wg-conf")?.value || "";
    if (!txt.trim()) { alert("El campo WireGuard esta vacio."); return; }
    navigator.clipboard?.writeText(txt).then(() => {
      wgCopyBtn.textContent = "Copiado!";
      setTimeout(() => { wgCopyBtn.textContent = "Copiar config"; }, 1500);
    });
  });

  const mountBtn = document.getElementById("smb-mount-btn");
  if (mountBtn) mountBtn.addEventListener("click", mountSmb);

  const umountBtn = document.getElementById("smb-umount-btn");
  if (umountBtn) umountBtn.addEventListener("click", unmountSmb);

  initPcBrowser();
  initPcDstBrowser();
  initCopyCommands();
  if (window.IX2_ROLE !== "viewer") refreshMounts();  // /api/mounts exige operator
  loadBackupsData();
  setInterval(() => { if (!_pollInterval) loadBackupsData(); }, 30000);

  const keygenBtn = document.getElementById('ssh-keygen-btn');
  if (keygenBtn) keygenBtn.addEventListener('click', generateSshKey);
  initWgGen();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initBackupsUi);
} else {
  initBackupsUi();
}

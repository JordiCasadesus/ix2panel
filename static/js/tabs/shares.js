let sharesCache = [];
let groupsCache = [];
let selectedShareName = null;
let canManageShares = false;
let shareBrowser = null;

function loadGroupsCache() {
  return fetch("/api/groups")
    .then(r => r.json())
    .then(data => {
      groupsCache = Array.isArray(data) ? data : [];
      // Si ya hay un share seleccionado, re-renderizar con los grupos frescos
      // (evita que la primera carga muestre grupos como "no encontrado").
      const current = sharesCache.find(s => s.name === selectedShareName);
      if (current) renderShareAccessSummary(current);
    })
    .catch(() => {
      groupsCache = [];
    });
}

// Resuelve usuarios directos + miembros de cada grupo (via groupsCache) para
// mostrar de un vistazo quien tiene acceso real a un share, no solo el
// nombre del grupo. Un usuario que aparece tanto directo como por grupo se
// muestra una sola vez, marcado como directo.
function renderShareAccessSummary(share) {
  const box = document.getElementById("share-access-summary");
  if (!box) return;
  box.innerHTML = "";

  const directUsers = share.users || [];
  const groupNames = share.groups || [];

  const seen = new Map(); // username -> array de origenes ("directo" o nombre de grupo)
  directUsers.forEach(u => seen.set(u, ["directo"]));

  const emptyGroupChips = [];
  groupNames.forEach(gname => {
    const g = groupsCache.find(x => x.name === gname);
    const members = g ? g.members : null;
    if (!members || !members.length) {
      // Grupo sin miembros conocidos (o no encontrado): mostrar el grupo tal cual.
      emptyGroupChips.push(`${gname} (grupo${g ? " vacio" : " no encontrado"})`);
      return;
    }
    members.forEach(u => {
      if (seen.has(u)) {
        if (!seen.get(u).includes(gname)) seen.get(u).push(gname);
      } else {
        seen.set(u, [gname]);
      }
    });
  });

  if (!seen.size && !emptyGroupChips.length) {
    box.innerHTML = '<div class="share-access-empty">Nadie tiene acceso todavia.</div>';
    return;
  }

  emptyGroupChips.forEach(text => {
    const chip = document.createElement("span");
    chip.className = "share-access-chip via-group";
    chip.textContent = text;
    box.appendChild(chip);
  });

  seen.forEach((origins, user) => {
    const chip = document.createElement("span");
    const isDirect = origins.includes("directo");
    chip.className = "share-access-chip" + (isDirect ? "" : " via-group");
    const via = origins.filter(o => o !== "directo");
    chip.textContent = via.length ? `${user} (${via.join(", ")})` : user;
    chip.title = isDirect && !via.length
      ? "Acceso directo"
      : isDirect
        ? `Acceso directo, tambien via grupo: ${via.join(", ")}`
        : `Acceso via grupo: ${via.join(", ")}`;
    box.appendChild(chip);
  });
}

function parseCsv(input) {
  return (input || "")
    .split(",")
    .map(x => x.trim())
    .filter(Boolean);
}

function renderShares() {
  const list = document.getElementById("shares-list");
  if (!list) return;
  fetch("/api/shares")
    .then(r => r.json())
    .then(data => {
      const items = Array.isArray(data.items) ? data.items : [];
      sharesCache = items;
      applyShareFilter();
    })
    .catch(err => {
      list.innerHTML = `<div class="process-item">Error: ${escHtml(err)}</div>`;
    });
}

function renderSharesList(items) {
  const list = document.getElementById("shares-list");
  if (!list) return;
  list.innerHTML = "";
  items.forEach(share => {
    const row = document.createElement("div");
    row.className = "share-row row-interactive";
    row.innerHTML = `
      <div class="share-name">${escHtml(share.name)}</div>
      <div class="share-path">${escHtml(share.path)}</div>
      <div class="share-source">${escHtml(share.source)}</div>
    `;
    row.addEventListener("click", () => {
      list.querySelectorAll(".share-row.active").forEach(el => el.classList.remove("active"));
      row.classList.add("active");
      selectedShareName = share.name;
      showSharePanel(share);
    });
    if (selectedShareName && selectedShareName === share.name) {
      row.classList.add("active");
      showSharePanel(share);
    }
    list.appendChild(row);
  });
}

function applyShareFilter() {
  const q = (document.getElementById("share-filter")?.value || "").trim().toLowerCase();
  const filtered = q
    ? sharesCache.filter(s => (s.name || "").toLowerCase().includes(q) || (s.path || "").toLowerCase().includes(q))
    : sharesCache.slice();
  renderSharesList(filtered);
}

function setShareEditEnabled(enabled) {
  ["share-name", "share-path", "share-path-tree", "share-read-only", "share-users", "share-groups", "share-save", "share-path-refresh"].forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    el.disabled = !enabled;
  });
}

function setShareDeleteEnabled(enabled) {
  const btn = document.getElementById("share-delete");
  if (btn) btn.disabled = !enabled;
}

// "view": solo lectura, es lo que se ve al seleccionar un share en la lista.
// "edit"/"new": formulario editable, solo visible tras pulsar Modificar/Nuevo share.
let shareMode = "view";

function setShareMode(mode) {
  shareMode = mode;
  const isEditing = mode === "edit" || mode === "new";
  const current = mode === "new" ? null : sharesCache.find(s => s.name === selectedShareName);

  document.getElementById("share-view-fields")?.classList.toggle("hidden", isEditing);
  document.getElementById("share-edit-fields")?.classList.toggle("hidden", !isEditing);

  const canModify = canManageShares && (mode === "new" || (current && current.editable));
  document.getElementById("share-modify")?.classList.toggle("hidden", isEditing || !canModify);
  document.getElementById("share-save")?.classList.toggle("hidden", !isEditing);
  document.getElementById("share-cancel-edit")?.classList.toggle("hidden", !isEditing);
  document.getElementById("share-delete")?.classList.toggle("hidden", isEditing);

  // Arbol de carpetas: fuera de edicion/nuevo, solo muestra donde esta el
  // share (bloqueado, sin poder navegar); al modificar o crear se desbloquea.
  document.getElementById("share-path-tree")?.classList.toggle("locked", !isEditing);
  document.getElementById("share-path-crumb")?.classList.toggle("locked", !isEditing);
  document.getElementById("share-path-actions")?.classList.toggle("hidden", !isEditing);

  setShareEditEnabled(isEditing && canManageShares);
}

// Arbol de carpetas: arbol lazy compartido (path_tree.js), cargando cada
// nivel bajo demanda. "/api/fs/list?path=/" ya filtra en el backend a las
// raices permitidas (/data1, /data2, /mnt, /media, /run/media, /home, /srv),
// asi que basta con anclar el arbol en "/" — sin limite de profundidad.
function getShareBrowser() {
  const tree = document.getElementById("share-path-tree");
  if (!tree || !window.PathTreeUI?.createLazyBrowser) return null;
  if (!shareBrowser) {
    const hint = document.getElementById("share-path-hint");
    if (hint) hint.textContent = "";
    shareBrowser = window.PathTreeUI.createLazyBrowser({
      container: tree,
      crumbEl: document.getElementById("share-path-crumb"),
      root: "/",
      onSelect: (path) => {
        const input = document.getElementById("share-path");
        if (input) input.value = path;
      },
    });
  }
  return shareBrowser;
}

function syncSharePathTree(path) {
  const browser = getShareBrowser();
  if (!browser) return;
  if (path) browser.setSelected(path);
}

function showSharePanel(share) {
  const panel = document.getElementById("share-panel");
  if (!panel) return;
  panel.classList.remove("hidden");

  document.getElementById("panel-share-name").textContent = share.name || "Share";
  document.getElementById("share-name").value = share.name || "";
  document.getElementById("share-path").value = share.path || "";
  document.getElementById("share-read-only").checked = !!share.read_only;
  document.getElementById("share-users").value = (share.users || []).join(", ");
  document.getElementById("share-groups").value = (share.groups || []).join(", ");
  document.getElementById("share-view-name").textContent = share.name || "-";
  document.getElementById("share-view-path").textContent = share.path || "-";
  document.getElementById("share-view-readonly").textContent = share.read_only ? "Si" : "No";
  const sourceLabel = share.source === "panel"
    ? "Creada desde el panel"
    : share.source === "system"
      ? "Definida en smb.conf (fuera del panel)"
      : (share.source || "-");
  document.getElementById("share-source").textContent = sourceLabel;
  document.getElementById("share-editable").textContent = share.editable ? "Si" : "No (solo se puede eliminar)";
  renderShareAccessSummary(share);

  setShareDeleteEnabled(canManageShares && (!!share.editable || share.source === "system"));
  syncSharePathTree(share.path || "");
  setShareMode("view");
}

function clearShareForm() {
  const panel = document.getElementById("share-panel");
  if (panel) panel.classList.remove("hidden");
  selectedShareName = null;
  document.getElementById("panel-share-name").textContent = "Nuevo share";
  document.getElementById("share-name").value = "";
  document.getElementById("share-path").value = "";
  document.getElementById("share-read-only").checked = false;
  document.getElementById("share-users").value = "";
  document.getElementById("share-groups").value = "";
  document.getElementById("share-source").textContent = "panel";
  document.getElementById("share-editable").textContent = "Si";
  const summaryBox = document.getElementById("share-access-summary");
  if (summaryBox) summaryBox.innerHTML = "";
  setShareDeleteEnabled(false);
  setShareMode("new");
  getShareBrowser();
}

function saveShare() {
  const name = (document.getElementById("share-name")?.value || "").trim();
  const path = (document.getElementById("share-path")?.value || "").trim();
  const readOnly = !!document.getElementById("share-read-only")?.checked;
  const users = parseCsv(document.getElementById("share-users")?.value || "");
  const groups = parseCsv(document.getElementById("share-groups")?.value || "");
  if (!name || !path) {
    alert("Nombre y ruta son obligatorios.");
    return;
  }
  const current = sharesCache.find(s => s.name === selectedShareName);
  const endpoint = current && current.editable ? "/api/shares/update" : "/api/shares/create";
  fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, path, read_only: readOnly, users, groups })
  })
    .then(r => r.json())
    .then(data => {
      if (data.error) {
        alert(data.error);
        return;
      }
      if (data.warning) alert(data.warning);
      selectedShareName = name;
      renderShares();
    })
    .catch(err => alert(String(err)));
}

function deleteShareSelected() {
  const current = sharesCache.find(s => s.name === selectedShareName);
  if (!current) {
    alert("Selecciona un share.");
    return;
  }
  if (!current.editable && current.source !== "system") {
    alert("Este share no se puede eliminar desde el panel.");
    return;
  }
  if (!confirm(`Eliminar share ${current.name}?`)) return;
  fetch("/api/shares/delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: current.name })
  })
    .then(r => r.json())
    .then(data => {
      if (data.error) {
        alert(data.error);
        return;
      }
      clearShareForm();
      renderShares();
    })
    .catch(err => alert(String(err)));
}

function cancelShareEdit() {
  if (shareMode === "new") {
    const panel = document.getElementById("share-panel");
    if (panel) panel.classList.add("hidden");
    document.querySelectorAll("#shares-list .share-row.active").forEach(el => el.classList.remove("active"));
    selectedShareName = null;
    return;
  }
  const current = sharesCache.find(s => s.name === selectedShareName);
  if (current) showSharePanel(current);
}

function reloadSambaConfig() {
  fetch("/api/shares/reload", { method: "POST" })
    .then(r => r.json())
    .then(data => {
      if (data.error) {
        alert(data.error);
        return;
      }
      alert("Samba recargado.");
    })
    .catch(err => alert(String(err)));
}

document.addEventListener("DOMContentLoaded", () => {
  if (document.querySelector("form.login-form")) return;
  if (window.IX2_ROLE === "viewer") return;  // viewer: sin acceso a esta pestana ni a su API
  fetch("/api/auth/me")
    .then(r => r.json())
    .then(me => {
      canManageShares = (me.role || "") === "admin";
    })
    .catch(() => {
      canManageShares = false;
    });

  renderShares();
  loadGroupsCache();
  document.getElementById("share-filter")?.addEventListener("input", applyShareFilter);
  document.getElementById("share-new")?.addEventListener("click", clearShareForm);
  document.getElementById("share-save")?.addEventListener("click", saveShare);
  document.getElementById("share-delete")?.addEventListener("click", deleteShareSelected);
  document.getElementById("share-modify")?.addEventListener("click", () => setShareMode("edit"));
  document.getElementById("share-cancel-edit")?.addEventListener("click", cancelShareEdit);
  document.getElementById("share-reload")?.addEventListener("click", reloadSambaConfig);
  document.getElementById("share-path-refresh")?.addEventListener("click", () => {
    const path = (document.getElementById("share-path")?.value || "/").trim() || "/";
    getShareBrowser()?.refresh(path);
  });

  document.querySelector('.tabs a[data-tab="compartidas"]')?.addEventListener("click", () => {
    renderShares();
    loadGroupsCache();
    getShareBrowser();
  });

  getShareBrowser();
});

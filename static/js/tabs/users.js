// =====================================================
// Users tab - backend-driven
// =====================================================


// =====================================================
// RENDER USERS LIST
// =====================================================
function renderUsers() {
  const list = document.getElementById("users-list");
  if (!list) return;

  list.innerHTML = "";

  fetch("/api/users")
    .then(r => r.json())
    .then(users => {
      if (!Array.isArray(users)) {
        list.innerHTML = `<div class="process-item">Sin datos</div>`;
        return;
      }
      window.lastUsersList = users;
      const names = new Set(users.map(u => u.name));
      if (window.lastSelectedUserName && !names.has(window.lastSelectedUserName)) {
        window.lastSelectedUserName = null;
        window.lastSelectedUser = null;
        clearUserPanel();
      }
      users.forEach(user => {
        const row = document.createElement("div");
        row.className = "user-row row-interactive";

        row.innerHTML = `
          <div class="user-name">${escHtml(user.name)}</div>
          <div class="user-role">${escHtml(user.role)}</div>
          <div class="user-status ${escHtml(user.status)}">${escHtml(user.status)}</div>
        `;

        row.addEventListener("click", () => {
          document
            .querySelectorAll("#tab-usuarios .user-row.active")
            .forEach(el => el.classList.remove("active"));

          row.classList.add("active");
          window.lastSelectedUserName = user.name;
          window.lastSelectedUser = user;
          showUserPanel(user);
        });

        if (window.lastSelectedUserName === user.name) {
          row.classList.add("active");
          window.lastSelectedUser = user;
          showUserPanel(user);
        }

        list.appendChild(row);
      });
    })
    .catch(err => {
      list.innerHTML = `<div class="process-item">Error: ${escHtml(err)}</div>`;
    });
}

// =====================================================
// RIGHT PANEL
// =====================================================
function showUserPanel(user) {
  const panel = document.getElementById("user-panel");
  panel.classList.remove("hidden");
  hideDeleteOptions();

  // Identidad
  const panelName = document.getElementById("panel-user-name");
  if (panelName) panelName.textContent = user.name;
  document.getElementById("user-name").textContent = user.name;
  document.getElementById("user-uid").textContent = user.uid ?? "—";
  document.getElementById("user-type").textContent =
    user.role === "admin" ? "Administrador" : "Usuario";

  // Estado
  const state = document.getElementById("user-state");
  if (state) state.textContent = user.status || "—";

  // Home
  const home = document.getElementById("user-home");
  home.textContent = user.home || `/home/${user.name}`;

  // Grupos
  const groupsEl = document.getElementById("user-groups");
  if (groupsEl) {
    groupsEl.innerHTML = "";
    const groups = Array.isArray(user.groups) ? user.groups : [];
    if (!groups.length) {
      groupsEl.innerHTML = `<div class="group">users</div>`;
    } else {
      groups.forEach((g, idx) => {
        const item = document.createElement("div");
        item.className = "group" + (idx === 0 ? " main" : "");
        item.textContent = g + (idx === 0 ? " (principal)" : "");
        groupsEl.appendChild(item);
      });
    }
  }
}

function clearUserPanel() {
  const panel = document.getElementById("user-panel");
  if (panel) panel.classList.add("hidden");
  hideDeleteOptions();
  const fields = [
    "panel-user-name",
    "user-name",
    "user-uid",
    "user-type",
    "user-state",
    "user-home"
  ];
  fields.forEach(id => {
    const el = document.getElementById(id);
    if (el) el.textContent = "—";
  });
  const groupsEl = document.getElementById("user-groups");
  if (groupsEl) groupsEl.innerHTML = "";
}

// =====================================================
// MODAL
// =====================================================
function openUserModal(mode, user) {
  const modal = document.getElementById("user-modal");
  if (!modal) return;
  modal.classList.remove("hidden");

  const title = document.getElementById("user-modal-title");
  if (title) title.textContent = mode === "edit" ? "Modificar usuario" : "Nuevo usuario";

  const nameInput = document.getElementById("user-form-name");
  const passInput = document.getElementById("user-form-pass");
  const groupsInput = document.getElementById("user-form-groups");
  const adminInput = document.getElementById("user-form-admin");
  const activeInput = document.getElementById("user-form-active");

  if (mode === "edit" && user) {
    nameInput.value = user.name || "";
    nameInput.disabled = true;
    passInput.value = "";
    groupsInput.value = Array.isArray(user.groups) ? user.groups.join(",") : "";
    adminInput.checked = user.role === "admin";
    activeInput.checked = (user.status || "") === "activo";
  } else {
    nameInput.value = "";
    nameInput.disabled = false;
    passInput.value = "";
    groupsInput.value = "";
    adminInput.checked = false;
    activeInput.checked = true;
  }

  modal.dataset.mode = mode;
}

function closeUserModal() {
  const modal = document.getElementById("user-modal");
  if (modal) modal.classList.add("hidden");
}

function saveUserFromModal() {
  const modal = document.getElementById("user-modal");
  if (!modal) return;
  const mode = modal.dataset.mode || "create";

  const name = document.getElementById("user-form-name").value.trim();
  const password = document.getElementById("user-form-pass").value;
  const groupsRaw = document.getElementById("user-form-groups").value;
  const admin = document.getElementById("user-form-admin").checked;
  const active = document.getElementById("user-form-active").checked;

  const groups = groupsRaw
    .split(",")
    .map(s => s.trim())
    .filter(Boolean);

  if (!name) {
    alert("Indica un nombre de usuario.");
    return;
  }
  if (mode !== "edit" && !/^[a-z_][a-z0-9_-]{0,31}$/.test(name)) {
    alert("Nombre de usuario invalido: solo minusculas, numeros, '_' o '-', empezando por letra o '_' (maximo 32 caracteres).");
    return;
  }

  const url = mode === "edit" ? "/api/users/update" : "/api/users/create";
  const payload = { name, password, admin, groups, active };

  fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  })
    .then(r => r.json())
    .then(res => {
      if (res.error) {
        alert(res.error);
        return;
      }
      closeUserModal();
      renderUsers();
      if (typeof loadUsersForGroups === "function") {
        loadUsersForGroups();
      }
    })
    .catch(err => alert(err));
}

function deleteSelectedUser() {
  const user = window.lastSelectedUser;
  if (!user || !user.name) return;

  const removeHome = !!document.getElementById("delete-opt-home")?.checked;
  const removePersonalShares = !!document.getElementById("delete-opt-shares")?.checked;
  const removeSharePaths = !!document.getElementById("delete-opt-share-paths")?.checked;

  fetch("/api/users/delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: user.name,
      remove_home: removeHome,
      remove_personal_shares: removePersonalShares,
      remove_share_paths: removeSharePaths
    })
  })
    .then(r => r.json())
    .then(res => {
      if (res.error) {
        alert(res.error);
        return;
      }
      if (res.warning) {
        alert(res.warning);
      } else if (Array.isArray(res.removed_shares) && res.removed_shares.length) {
        let msg = `Usuario eliminado. Shares borrados: ${res.removed_shares.join(", ")}`;
        if (Array.isArray(res.removed_paths) && res.removed_paths.length) {
          msg += `\nCarpetas borradas: ${res.removed_paths.join(", ")}`;
        }
        if (Array.isArray(res.share_path_errors) && res.share_path_errors.length) {
          msg += `\nAvisos: ${res.share_path_errors.join(" | ")}`;
        }
        alert(msg);
      }
      window.lastSelectedUserName = null;
      window.lastSelectedUser = null;
      hideDeleteOptions();
      clearUserPanel();
      renderUsers();
      if (typeof loadUsersForGroups === "function") loadUsersForGroups();
      if (typeof renderGroups === "function") renderGroups();
    })
    .catch(err => alert(err));
}

function showDeleteOptions() {
  const box = document.getElementById("user-delete-options");
  if (box) box.classList.remove("hidden");
}

function hideDeleteOptions() {
  const box = document.getElementById("user-delete-options");
  if (box) box.classList.add("hidden");
}

function startDeleteSelectedUser() {
  let user = window.lastSelectedUser;
  if (!user || !user.name) {
    const name = (document.getElementById("user-name")?.textContent || "").trim();
    if (name && name !== "—") {
      user = { name };
      window.lastSelectedUser = user;
    }
  }
  if (!user || !user.name) {
    alert("Selecciona un usuario primero.");
    return;
  }
  showDeleteOptions();
}

function bindUserDeleteOptions() {
  const sharesOpt = document.getElementById("delete-opt-shares");
  const pathsOpt = document.getElementById("delete-opt-share-paths");
  if (!sharesOpt || !pathsOpt) return;

  const sync = () => {
    if (!sharesOpt.checked) {
      pathsOpt.checked = false;
      pathsOpt.disabled = true;
    } else {
      pathsOpt.disabled = false;
    }
  };

  sharesOpt.addEventListener("change", sync);
  sync();
}

// =====================================================
// INIT
// =====================================================
document.addEventListener("DOMContentLoaded", () => {
  if (document.querySelector("form.login-form")) return;
  if (window.IX2_ROLE === "viewer") return;  // viewer: sin acceso a esta pestana ni a su API
  renderUsers();

  const btnNew = document.getElementById("btn-new-user");
  if (btnNew) btnNew.addEventListener("click", () => openUserModal("create"));

  const btnEdit = document.getElementById("btn-edit-user");
  if (btnEdit) btnEdit.addEventListener("click", () => {
    const user = window.lastSelectedUser;
    if (!user) return;
    // Refresh user data before editing (groups may have changed)
    fetch("/api/users")
      .then(r => r.json())
      .then(users => {
        if (Array.isArray(users)) {
          const fresh = users.find(u => u.name === user.name);
          if (fresh) {
            window.lastSelectedUser = fresh;
            openUserModal("edit", fresh);
            return;
          }
        }
        openUserModal("edit", user);
      })
      .catch(() => openUserModal("edit", user));
  });

  const btnDelete = document.getElementById("btn-delete-user");
  if (btnDelete) btnDelete.addEventListener("click", startDeleteSelectedUser);
  const btnDeleteConfirm = document.getElementById("btn-delete-user-confirm");
  if (btnDeleteConfirm) btnDeleteConfirm.addEventListener("click", deleteSelectedUser);
  const btnDeleteCancel = document.getElementById("btn-delete-user-cancel");
  if (btnDeleteCancel) btnDeleteCancel.addEventListener("click", hideDeleteOptions);

  const modalClose = document.getElementById("user-modal-close");
  if (modalClose) modalClose.addEventListener("click", closeUserModal);
  const modalCancel = document.getElementById("user-form-cancel");
  if (modalCancel) modalCancel.addEventListener("click", closeUserModal);
  const modalSave = document.getElementById("user-form-save");
  if (modalSave) modalSave.addEventListener("click", saveUserFromModal);
  bindUserDeleteOptions();
});

// Delegated handlers: tabs can be mounted dynamically after DOMContentLoaded.
document.addEventListener("click", (e) => {
  const btn = e.target?.closest ? e.target.closest("button[data-action]") : null;
  const action = btn?.dataset?.action;
  if (action === "user-delete-open") {
    startDeleteSelectedUser();
  } else if (action === "user-delete-confirm") {
    deleteSelectedUser();
  } else if (action === "user-delete-cancel") {
    hideDeleteOptions();
  }
});

document.addEventListener("change", (e) => {
  if (e.target?.id !== "delete-opt-shares") return;
  const sharesOpt = document.getElementById("delete-opt-shares");
  const pathsOpt = document.getElementById("delete-opt-share-paths");
  if (!sharesOpt || !pathsOpt) return;
  if (!sharesOpt.checked) {
    pathsOpt.checked = false;
    pathsOpt.disabled = true;
  } else {
    pathsOpt.disabled = false;
  }
});

// Expose for debugging / fallback bindings from template if needed.
window.startDeleteSelectedUser = startDeleteSelectedUser;
window.deleteSelectedUser = deleteSelectedUser;
window.hideDeleteOptions = hideDeleteOptions;

// =====================================================
// Groups tab – backend-driven
// =====================================================

let lastSelectedGroup = null;
let cachedUsers = [];
let cachedGroups = [];
let currentGroup = null;

function renderGroups() {
  const list = document.getElementById("groups-list");
  if (!list) return;

  list.innerHTML = "";
  fetch("/api/groups")
    .then(r => r.json())
    .then(groups => {
      if (!Array.isArray(groups)) {
        list.innerHTML = `<div class="process-item">Sin datos</div>`;
        return;
      }
      cachedGroups = groups;
      applyGroupFilter();
    })
    .catch(err => {
      list.innerHTML = `<div class="process-item">Error: ${escHtml(err)}</div>`;
    });
}

function renderGroupsList(groups) {
  const list = document.getElementById("groups-list");
  if (!list) return;
  list.innerHTML = "";
  groups.forEach(group => {
    const row = document.createElement("div");
    row.className = "group-row row-interactive";
    row.innerHTML = `
      <div class="group-name">${escHtml(group.name)}</div>
      <div class="group-gid">${escHtml(group.gid)}</div>
      <div class="group-count">${group.members.length}</div>
    `;

    row.addEventListener("click", () => {
      list
        .querySelectorAll(".group-row.active")
        .forEach(el => el.classList.remove("active"));
      row.classList.add("active");
      lastSelectedGroup = group.name;
      showGroupPanel(group);
    });

    if (lastSelectedGroup === group.name) {
      row.classList.add("active");
      showGroupPanel(group);
    }

    list.appendChild(row);
  });
}

function applyGroupFilter() {
  const input = document.getElementById("group-filter");
  const q = (input?.value || "").trim().toLowerCase();
  // Los grupos de sistema (GID < 1000 y sin miembros) se ocultan salvo que se pida verlos
  const showSys = !!document.getElementById("group-show-system")?.checked;
  const isSystem = g => Number(g.gid) < 1000 && !(g.members || []).length;
  const hiddenCount = cachedGroups.filter(isSystem).length;
  const counter = document.getElementById("group-system-count");
  if (counter) counter.textContent = hiddenCount ? `(${hiddenCount})` : "";
  let base = showSys ? cachedGroups.slice() : cachedGroups.filter(g => !isSystem(g));
  if (q) base = cachedGroups.filter(g => (g.name || "").toLowerCase().includes(q));  // al buscar se mira en todos
  renderGroupsList(base);
}

function showGroupPanel(group) {
  const panel = document.getElementById("group-panel");
  if (panel) panel.classList.remove("hidden");

  currentGroup = group;
  document.getElementById("panel-group-name").textContent = group.name;
  document.getElementById("group-name").textContent = group.name;
  document.getElementById("group-gid").textContent = group.gid;

  const members = document.getElementById("group-members");
  members.innerHTML = "";
  if (!group.members.length) {
    members.innerHTML = `<div class="member-item">Sin miembros</div>`;
  } else {
    const primary = new Set(group.members_primary || []);
    const filterInput = document.getElementById("group-member-filter");
    const q = (filterInput?.value || "").trim().toLowerCase();
    const filtered = q
      ? group.members.filter(m => (m || "").toLowerCase().includes(q))
      : group.members.slice();
    filtered.forEach(m => {
      const item = document.createElement("div");
      item.className = "member-item";
      const tag = primary.has(m) ? "principal" : "secundario";
      item.innerHTML = `
        <span class="member-name">${escHtml(m)}</span>
        <span class="member-tag ${tag}">${tag}</span>
        ${primary.has(m) ? "" : `<button class="member-remove" title="Quitar del grupo" data-user="${escHtml(m)}">✕</button>`}
      `;
      members.appendChild(item);
    });
  }

  const sel = document.getElementById("group-user-select");
  if (sel && cachedUsers.length) {
    sel.innerHTML = cachedUsers.map(u => `<option value="${escHtml(u.name)}">${escHtml(u.name)}</option>`).join("");
  }
}

function loadUsersForGroups() {
  fetch("/api/users")
    .then(r => r.json())
    .then(users => {
      cachedUsers = Array.isArray(users) ? users : [];
      const sel = document.getElementById("group-user-select");
      if (sel) {
        sel.innerHTML = cachedUsers.map(u => `<option value="${escHtml(u.name)}">${escHtml(u.name)}</option>`).join("");
      }
    });
}

function createGroup() {
  const input = document.getElementById("new-group-name");
  const name = (input?.value || "").trim();
  if (!name) return;
  fetch("/api/groups/create", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name })
  })
    .then(r => r.json())
    .then(data => {
      if (data.error) {
        alert(data.error);
        return;
      }
      input.value = "";
      renderGroups();
    });
}

function addUserToGroup() {
  const sel = document.getElementById("group-user-select");
  if (!sel || !lastSelectedGroup) return;
  const user = sel.value;
  fetch("/api/groups/add-user", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user, group: lastSelectedGroup })
  })
    .then(r => r.json())
    .then(data => {
      if (data.error) {
        alert(data.error);
        return;
      }
      renderGroups();
      if (typeof renderUsers === "function") renderUsers();
    });
}

function removeUserFromGroup(user) {
  if (!lastSelectedGroup) return;
  fetch("/api/groups/remove-user", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user, group: lastSelectedGroup })
  })
    .then(r => r.json())
    .then(data => {
      if (data.error) {
        alert(data.error);
        return;
      }
      renderGroups();
      if (typeof renderUsers === "function") renderUsers();
    });
}

document.addEventListener("DOMContentLoaded", () => {
  if (document.querySelector("form.login-form")) return;
  if (window.IX2_ROLE === "viewer") return;  // viewer: sin acceso a esta pestana ni a su API
  renderGroups();
  loadUsersForGroups();
  document.getElementById("btn-new-group")?.addEventListener("click", createGroup);
  document.getElementById("group-add-user")?.addEventListener("click", addUserToGroup);
  document.getElementById("group-filter")?.addEventListener("input", applyGroupFilter);
  document.getElementById("group-member-filter")?.addEventListener("input", () => {
    if (currentGroup) showGroupPanel(currentGroup);
  });

  document.getElementById("group-members")?.addEventListener("click", (e) => {
    const target = e.target;
    if (target && target.classList.contains("member-remove")) {
      const user = target.getAttribute("data-user");
      if (user) {
        if (!confirm(`Quitar ${user} del grupo ${lastSelectedGroup}?`)) return;
        removeUserFromGroup(user);
      }
    }
  });
});

// Refresh user list whenever the Groups tab is opened
document.addEventListener("DOMContentLoaded", () => {
  if (document.querySelector("form.login-form")) return;
  const tab = document.querySelector('.tabs a[data-tab="grupos"]');
  if (tab) {
    tab.addEventListener("click", () => {
      loadUsersForGroups();
    });
  }
});

document.addEventListener("DOMContentLoaded", () => {
  const chk = document.getElementById("group-show-system");
  if (chk) chk.addEventListener("change", applyGroupFilter);
});

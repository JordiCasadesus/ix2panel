// =====================================================
// Disks tab â€“ UI logic (Linux-first)
// =====================================================

// -----------------------------------------------------
// Role â†’ CSS class mapping
// -----------------------------------------------------
const roleMap = {
  "Sistema": "system",
  "Datos": "data",
  "EFI": "efi",
  "RecuperaciÃ³n": "rec",
  "USB": "usb",
  "No montado": "data"
};

function getDeviceBase(name) {
  if (!name) return "";
  let base = String(name).replace("/dev/", "");
  if (base.startsWith("nvme")) return base.replace(/p?\d+$/, "");
  if (base.startsWith("mmcblk")) return base.replace(/p?\d+$/, "");
  return base.replace(/\d+$/, "");
}

function getDeviceShort(name) {
  if (!name) return "";
  return String(name).replace("/dev/", "");
}

function drawDiskWave(canvas, history) {
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  const w = canvas.clientWidth || canvas.width;
  const h = canvas.clientHeight || canvas.height;
  if (canvas.width !== w) canvas.width = w;
  if (canvas.height !== h) canvas.height = h;
  ctx.clearRect(0, 0, w, h);

  const points = Array.isArray(history) ? history : [];
  const maxVal = Math.max(1, ...points.map(p => Math.max(p.r || 0, p.w || 0)));

  const styles = getComputedStyle(canvas);
  const rxColor = styles.getPropertyValue("--wave-rx").trim() || "#5ac8fa";
  const txColor = styles.getPropertyValue("--wave-tx").trim() || "#7ee081";

  const drawSeries = (key, color) => {
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.0;
    ctx.beginPath();
    const n = points.length;
    for (let i = 0; i < n; i++) {
      const v = points[i][key] || 0;
      const x = n > 1 ? (i / (n - 1)) * (w - 2) + 1 : 1;
      const y = h - 2 - (v / maxVal) * (h - 4);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
  };

  drawSeries("r", rxColor);
  drawSeries("w", txColor);
}

const _diskBrowsers = {};

function _getDiskBrowser(partitionKey) { return _diskBrowsers[partitionKey] || null; }


// =====================================================
// RENDER: Disks + partitions (LEFT PANEL)
// =====================================================
function renderDisksDetail(disks_tree) {
  const view = document.getElementById("disks-detail");
  if (!view) return;

  view.innerHTML = "";
  if (!window.lastSelectedPartitionKey) {
    const panel = document.getElementById("partition-panel");
    if (panel) panel.classList.add("hidden");
  }

  if (!Array.isArray(disks_tree) || disks_tree.length === 0) {
    view.textContent = "No hay discos";
    return;
  }

  disks_tree.forEach(disk => {
    // --- Disk group (physical disk) ---
    const group = document.createElement("div");
    group.className = "disk-group";

    const firstPart = (Array.isArray(disk.partitions) && disk.partitions.length) ? disk.partitions[0] : null;
    const baseDevice = firstPart ? getDeviceBase(firstPart.device) : "";
    const diskKey = String(disk.disk);

    group.innerHTML = `
      <div class="disk-group-header">
        <span class="disk-title">DISCO ${escHtml(disk.disk)}${baseDevice ? ` (${escHtml(baseDevice)})` : ""}</span>
      </div>
      <div class="disk-wave-wrap">
        <canvas class="disk-wave" width="280" height="48" data-disk="${escHtml(diskKey)}"></canvas>
        <div class="disk-wave-legend">
          <span class="wave-key rx">Lectura</span>
          <span class="wave-key tx">Escritura</span>
        </div>
      </div>
    `;

    const wave = group.querySelector(".disk-wave");
    const history = (window.diskIoHistory || {})[diskKey] || [];
    drawDiskWave(wave, history);

    // --- Partitions (rows) ---
    if (Array.isArray(disk.partitions)) {
      disk.partitions.forEach(partition => {
        const row = document.createElement("div");
        row.className = "partition-row row-interactive";

        const roleClass = roleMap[partition.role] || "data";
        const devShort = getDeviceShort(partition.device);
        const mountLabel = partition.mountpoint ? partition.mountpoint : "Sin montar";
        const actionBtn = partition.can_mount
          ? `<button class="partition-action" data-action="mount" data-device="${escHtml(partition.device)}">Montar</button>`
          : (partition.can_unmount
            ? `<button class="partition-action" data-action="unmount" data-target="${escHtml(partition.mountpoint || partition.device)}">Desmontar</button>`
            : "");
        row.innerHTML = `
          <div class="partition-name">
            ${escHtml(mountLabel)}
          </div>

          <div class="partition-bar">
            <div class="fill ${roleClass}" style="width:${escHtml(partition.percent)}%"></div>
          </div>

          <div class="partition-meta">
            ${escHtml(partition.used)} / ${escHtml(partition.total)} GB | ${escHtml(partition.fstype || "-")} | ${escHtml(partition.role)}${devShort ? ` | ${escHtml(devShort)}` : ""}
          </div>
          <div class="partition-actions">
            ${actionBtn}
          </div>
        `;

        // Select partition â†’ open right panel
        row.addEventListener("click", () => {
          document
            .querySelectorAll("#tab-disks .partition-row.active")
            .forEach(el => el.classList.remove("active"));

          row.classList.add("active");
          window.lastSelectedPartitionKey = `${disk.disk}:${partition.name}`;
          window._smartLoadedFor = null; // forzar recarga SMART al hacer clic
          showPartitionPanel(partition, disk);
        });

        if (window.lastSelectedPartitionKey === `${disk.disk}:${partition.name}`) {
          row.classList.add("active");
          showPartitionPanel(partition, disk, false); // false = no recargar SMART
        }

        group.appendChild(row);
      });
    }

    view.appendChild(group);
  });
}

// =====================================================
// RIGHT PANEL: Partition info
// =====================================================
function showPartitionPanel(partition, disk, loadSmart = true) {
  const panel = document.getElementById("partition-panel");
  panel.classList.remove("hidden");

  // Header
  const title = document.getElementById("panel-partition-name");
  const usage = document.getElementById("panel-partition-usage");
  const bar   = panel.querySelector(".panel-bar .fill");

  const baseDevice = getDeviceBase(partition.device);
  title.textContent = `Disco ${disk.disk}${baseDevice ? ` (${baseDevice})` : ""} - ${partition.name}`;
  usage.textContent = `${partition.used} / ${partition.total} GB (${partition.percent}%)`;

  // Mini usage bar
  const roleClass = roleMap[partition.role] || "data";
  bar.className = `fill ${roleClass}`;
  bar.style.width = `${partition.percent}%`;

  // Info block
  const info = document.getElementById("partition-info");
  const devShort = getDeviceShort(partition.device);
  info.innerHTML = `
    <div><strong>Disco:</strong> ${escHtml(disk.disk)}${baseDevice ? ` (${escHtml(baseDevice)})` : ""}</div>
    <div><strong>Particion:</strong> ${escHtml(partition.name)}</div>
    <div><strong>Rol:</strong> ${escHtml(partition.role)}</div>
    <div><strong>Sistema:</strong> ${escHtml(partition.fstype)}</div>
    ${devShort ? `<div><strong>Dispositivo:</strong> ${escHtml(devShort)}</div>` : ""}
    ${partition.mountpoint ? `<div><strong>Montaje:</strong> ${escHtml(partition.mountpoint)}</div>` : `<div><strong>Montaje:</strong> -</div>`}
    <div><strong>Uso:</strong> ${escHtml(partition.used)} / ${escHtml(partition.total)} GB</div>
  `;

  if (partition.mounted && partition.mountpoint) {
    const partitionKey = `${disk.disk}:${partition.mountpoint}`;
    window.selectedDiskPartitionKey = partitionKey;
    const tree = document.getElementById("disk-folder-tree");
    const crumb = document.getElementById("disk-folder-breadcrumb");
    if (tree && window.IX2_ROLE === "viewer") {
      tree.textContent = "Explorar carpetas: solo operator o admin";  // /api/fs/list exige operator
    } else if (tree && (loadSmart || !_diskBrowsers[partitionKey])) {
      const saved = (window.folderPathByPartition || {})[partitionKey];
      if (loadSmart) tree.innerHTML = "";
      const browser = window.PathTreeUI.createLazyBrowser({
        container: tree,
        crumbEl: crumb,
        root: partition.mountpoint,
        onSelect: (path) => {
          if (!window.folderPathByPartition) window.folderPathByPartition = {};
          window.folderPathByPartition[partitionKey] = path;
        },
      });
      _diskBrowsers[partitionKey] = browser;
      if (saved && saved !== partition.mountpoint) browser.setSelected(saved);
    }
  } else {
    window.selectedDiskPartitionKey = null;
    const tree = document.getElementById("disk-folder-tree");
    const crumb = document.getElementById("disk-folder-breadcrumb");
    if (crumb) crumb.textContent = "No montado";
    if (tree) tree.innerHTML = '<div class="tree-item"><span class="tree-caret">-</span><span class="tree-folder-icon">&#128193;</span><span class="tree-name">Sin montaje</span></div>';
  }

  // Enable folder actions once a partition is selected
  enableFolderActions();

  // Cargar SMART del disco padre (extraer /dev/sda de /dev/sda1)
  if (loadSmart) {
    const firstPart = disk.partitions && disk.partitions[0];
    const partDev = (firstPart && firstPart.device) || "";
    const smartDev = partDev ? partDev.replace(/[0-9]+$/, "") : "";
    loadSmartInfo(smartDev);
  }
}

function loadSmartInfo(diskDev) {
  const section = document.getElementById("smart-section");
  const info = document.getElementById("smart-info");
  if (!section || !info) return;
  section.style.display = "";
  info.innerHTML = '<div class="muted">Cargando...</div>';
  fetch("/api/disks/smart?device=" + encodeURIComponent(diskDev))
    .then(r => r.json()).then(d => {
      if (!d.available) { info.innerHTML = '<div class="muted">SMART no disponible</div>'; return; }
      const healthCls = d.health === "PASSED" ? "ok" : "err";
      info.innerHTML =
        '<div><strong>Estado:</strong> <span class="status ' + healthCls + '"></span> ' + escHtml(d.health) + '</div>' +
        '<div><strong>Modelo:</strong> ' + escHtml(d.model) + '</div>' +
        '<div><strong>Serie:</strong> ' + escHtml(d.serial) + '</div>' +
        '<div><strong>Velocidad:</strong> ' + escHtml(d.rpm) + '</div>' +
        '<div><strong>Horas encendido:</strong> ' + escHtml(d.hours) + '</div>' +
        '<div><strong>Temperatura:</strong> ' + escHtml(d.temp) + '</div>' +
        '<div><strong>Sectores reasignados:</strong> ' + escHtml(d.reallocated) + '</div>' +
        '<div><strong>Sectores pendientes:</strong> ' + escHtml(d.pending) + '</div>' +
        '<div><strong>Incorregibles:</strong> ' + escHtml(d.uncorrectable) + '</div>';
    }).catch(() => { info.innerHTML = '<div class="muted">Error cargando SMART</div>'; });
}

function getMountRootFromPartitionKey(partitionKey) {
  if (!partitionKey || !partitionKey.includes(":")) return "/";
  const path = partitionKey.split(":").slice(1).join(":") || "/";
  return window.PathTreeUI?.normalizePath ? window.PathTreeUI.normalizePath(path) : path;
}

function getCurrentFolderPath() {
  const key = window.selectedDiskPartitionKey;
  if (!key) return null;
  const browser = _diskBrowsers[key];
  if (browser) return browser.getSelected();
  const current = (window.folderPathByPartition || {})[key];
  return current || null;
}

function enableFolderActions() {
  const actions = document.getElementById("disk-folder-actions");
  const newBtn = document.getElementById("disk-folder-new-btn");
  const delBtn = document.getElementById("disk-folder-delete-btn");
  const createBox = document.getElementById("disk-folder-create");
  const createInput = document.getElementById("disk-folder-name");
  const createConfirm = document.getElementById("disk-folder-create-confirm");
  const createCancel = document.getElementById("disk-folder-create-cancel");

  if (actions) actions.classList.toggle("hidden", !window.selectedDiskPartitionKey);
  if (window.diskFolderActionsBound) return;
  window.diskFolderActionsBound = true;

  if (newBtn) {
    newBtn.addEventListener("click", () => {
      if (!window.selectedDiskPartitionKey) return;
      if (createBox) createBox.classList.remove("hidden");
      if (createInput) {
        createInput.value = "";
        createInput.focus();
      }
    });
  }

  if (createCancel) {
    createCancel.addEventListener("click", () => {
      if (createBox) createBox.classList.add("hidden");
    });
  }

  if (createConfirm) {
    createConfirm.addEventListener("click", () => {
      const key = window.selectedDiskPartitionKey;
      const parent = getCurrentFolderPath();
      const name = (createInput?.value || "").trim();
      if (!key || !parent) {
        alert("Selecciona una particion montada.");
        return;
      }
      if (!name) {
        alert("Escribe un nombre de carpeta.");
        return;
      }
      fetchJSON("/api/fs/mkdir", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ parent, name })
      }).then(res => {
        if (res.error) {
          alert(res.error);
          return;
        }
        if (createBox) createBox.classList.add("hidden");
        _diskBrowsers[key]?.refresh(parent);
      });
    });
  }

  if (delBtn) {
    delBtn.addEventListener("click", () => {
      const key = window.selectedDiskPartitionKey;
      const current = getCurrentFolderPath();
      if (!key || !current) {
        alert("Selecciona una carpeta.");
        return;
      }

      const mountRoot = getMountRootFromPartitionKey(key);
      if (current === mountRoot) {
        alert("No se puede eliminar la raiz montada de la particion.");
        return;
      }

      if (!confirm(`Eliminar carpeta ${current}?\nSe borrara todo su contenido. Esta seguro?`)) return;
      const parts = current.split("/").filter(Boolean);
      const parent = parts.length > 1 ? `/${parts.slice(0, -1).join("/")}` : mountRoot;
      fetchJSON("/api/fs/rmdir", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: current })
      }).then(res => {
        if (res.error) {
          alert(res.error);
          return;
        }
        if (!window.folderPathByPartition) window.folderPathByPartition = {};
        window.folderPathByPartition[key] = parent;
        _diskBrowsers[key]?.refresh(parent);
      });
    });
  }
}

document.addEventListener("click", (e) => {
  const btn = e.target;
  if (!btn || !btn.classList.contains("partition-action")) return;
  e.stopPropagation();
  const action = btn.getAttribute("data-action");
  if (action === "mount") {
    const device = btn.getAttribute("data-device");
    fetchJSON("/api/disks/mount", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device })
    }).then(res => {
      if (res.error) alert(res.error);
    });
  } else if (action === "unmount") {
    const target = btn.getAttribute("data-target");
    fetchJSON("/api/disks/unmount", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target })
    }).then(res => {
      if (res.error) alert(res.error);
    });
  }
});

// ---------------------------------------------------------------------------
// Test SMART largo programado
// ---------------------------------------------------------------------------

function renderSmartSchedule(data) {
  const enabled = document.getElementById("smart-sched-enabled");
  const weekday = document.getElementById("smart-sched-weekday");
  const time = document.getElementById("smart-sched-time");
  const status = document.getElementById("smart-sched-status");
  if (!enabled || !weekday || !time || !status) return;

  enabled.checked = !!data.enabled;
  weekday.value = String(data.weekday);
  time.value = data.time || "10:00";

  let html = '<div><strong>Próximo test:</strong> ' + escHtml(data.next_run || "—") + '</div>' +
             '<div><strong>Último lanzamiento:</strong> ' + escHtml(data.last_run || "—") + '</div>';

  (data.disks || []).forEach(d => {
    if (d.running) {
      html += '<div><strong>' + escHtml(d.device) + ':</strong> test en curso' +
              (d.remaining ? ' — ' + escHtml(d.remaining) + ' restante' : '') + '</div>';
    } else {
      html += '<div><strong>' + escHtml(d.device) + ':</strong> ' + escHtml(d.last_result || "—") +
              (d.last_hours ? ' (a las ' + escHtml(d.last_hours) + ')' : '') + '</div>';
    }
  });
  status.innerHTML = html;
}

function loadSmartSchedule() {
  if (!document.getElementById("smart-sched-status")) return;
  fetchJSON("/api/disks/smart/schedule").then(renderSmartSchedule).catch(() => {});
}

document.addEventListener("DOMContentLoaded", loadSmartSchedule);

document.addEventListener("click", (e) => {
  if (!e.target || !e.target.closest) return;

  // Refrescar al entrar en la pestana
  if (e.target.closest('.tabs a[data-tab="disks"]')) {
    setTimeout(loadSmartSchedule, 50);
    return;
  }

  if (e.target.id === "smart-sched-save") {
    fetchJSON("/api/disks/smart/schedule", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        enabled: document.getElementById("smart-sched-enabled").checked,
        weekday: parseInt(document.getElementById("smart-sched-weekday").value, 10),
        time: document.getElementById("smart-sched-time").value
      })
    }).then(res => {
      if (!res.ok) { alert(res.error || "No se pudo guardar"); return; }
      loadSmartSchedule();
    });
    return;
  }

  if (e.target.id === "smart-test-now") {
    if (!confirm("¿Lanzar ahora el test largo en todos los discos?\n\n" +
                 "Tarda un par de horas y se ejecuta en segundo plano: " +
                 "puedes seguir usando el NAS con normalidad.")) return;
    fetchJSON("/api/disks/smart/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({})
    }).then(res => {
      if (!res.ok) { alert(res.error || "No se pudo lanzar el test"); return; }
      const results = res.results || {};
      const lines = Object.keys(results).map(k => k + ": " + results[k]);
      alert(lines.length ? lines.join("\n") : "Test lanzado");
      loadSmartSchedule();
    });
  }
});




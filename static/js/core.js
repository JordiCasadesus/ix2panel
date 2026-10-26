


function fetchJSON(url, opts) {
  const merged = Object.assign({ credentials: "same-origin" }, opts || {});
  return fetch(url, merged).then(async r => {
    if (r.status === 401) {
      window.location.href = "/login";
      throw new Error("unauthorized");
    }
    // Algunos fallos de proxy/auth devuelven HTML; evitamos romper con JSON.parse.
    const contentType = (r.headers.get("content-type") || "").toLowerCase();
    const bodyText = await r.text();

    if (r.redirected && (r.url || "").includes("/login")) {
      window.location.href = "/login";
      throw new Error("unauthorized");
    }

    if (!r.ok) {
      let detail = bodyText.trim();
      if (detail.length > 200) detail = detail.slice(0, 200) + "...";
      throw new Error(`HTTP ${r.status}${detail ? `: ${detail}` : ""}`);
    }

    if (!contentType.includes("application/json")) {
      let preview = bodyText.trim();
      if (preview.length > 120) preview = preview.slice(0, 120) + "...";
      throw new Error(`Respuesta no JSON del servidor (${contentType || "sin content-type"}): ${preview || "(vacio)"}`);
    }

    try {
      return JSON.parse(bodyText);
    } catch (e) {
      throw new Error(`JSON invalido: ${e}`);
    }
  });
}

// Aviso cuando una accion falla (p.ej. 403 por rol insuficiente): fetchJSON lanza "HTTP 403: {...}".
function alertActionError(err) {
  const m = String((err && err.message) || err || "Error");
  if (m === "unauthorized") return;  // fetchJSON ya redirige al login
  if (m.indexOf("HTTP 403") === 0) { alert("No tienes permiso para hacer esto con tu rol."); return; }
  let text = m;
  const i = m.indexOf("{");
  if (i >= 0) {
    try { const j = JSON.parse(m.slice(i)); if (j && j.error) text = String(j.error); } catch (_) {}
  }
  alert(text);
}

// Escapa texto para meterlo en un innerHTML (& < > " '). Todo dato que venga del servidor,
// de un fichero o de un usuario y se interpole en HTML debe pasar por aqui; para texto plano, usar textContent.
function escHtml(v) {
  return String(v == null ? "" : v).replace(/[&<>"']/g, c => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function getDeviceBase(name) {
  if (!name) return "";
  let base = String(name).replace("/dev/", "");
  // nvme0n1p1 -> nvme0n1
  if (base.startsWith("nvme")) {
    return base.replace(/p?\d+$/, "");
  }
  // mmcblk0p1 -> mmcblk0
  if (base.startsWith("mmcblk")) {
    return base.replace(/p?\d+$/, "");
  }
  // sda1 -> sda, hda1 -> hda
  return base.replace(/\d+$/, "");
}

function buildDiskDeviceMap(disksTree) {
  const map = {};
  if (!Array.isArray(disksTree)) return map;
  disksTree.forEach(d => {
    if (!Array.isArray(d.partitions)) return;
    d.partitions.forEach(p => {
      const base = getDeviceBase(p.device || "");
      if (!base) return;
      if (!map[base]) {
        map[base] = { disk: String(d.disk), label: `DISCO ${d.disk}`, device: base };
      }
    });
  });
  return map;
}

function updateSystem() {
  fetchJSON("/api/system")
    .then(data => {
      if (data.error) return;
      window.lastSystemData = data;
      if (window.ix2DashLoaded) window.ix2DashLoaded();  // polish.js: quita el indicador de carga
      window.diskDeviceMap = buildDiskDeviceMap(data.disks_tree || []);


      // CPU
      const cpuPercentEl = document.getElementById("cpu-percent");
      const cpuBarEl = document.getElementById("cpu-bar");
      if (cpuPercentEl && data.cpu_percent !== undefined) {
        cpuPercentEl.textContent = data.cpu_percent.toFixed(1) + "%";
      }
      if (cpuBarEl && data.cpu_percent !== undefined) {
        cpuBarEl.style.width = data.cpu_percent + "%";
      }

      // RAM
      const ramInfoEl = document.getElementById("ram-info");
      const ramBarEl = document.getElementById("ram-bar");
      if (ramInfoEl && data.ram_used !== undefined && data.ram_total !== undefined) {
        ramInfoEl.textContent = `${data.ram_used} / ${data.ram_total} MB`;
      }
      if (ramBarEl && data.ram_percent !== undefined) {
        ramBarEl.style.width = data.ram_percent + "%";
      }

      // Uptime
      const uptimeEl = document.getElementById("dash-uptime");
      if (uptimeEl && data.uptime !== undefined) {
        uptimeEl.textContent = data.uptime;
      }

      // Red
      const ifaceEls = document.querySelectorAll(".net-iface");
      const ipEls = document.querySelectorAll(".net-ip");
      if (data.iface !== undefined) {
        ifaceEls.forEach(el => {
          el.textContent = data.iface;
        });
      }
      if (data.ip !== undefined) {
        ipEls.forEach(el => {
          el.textContent = data.ip;
        });
      }

      // Red campos extra
      if (data.gateway !== undefined) document.querySelectorAll(".net-gateway").forEach(el => { el.textContent = data.gateway || "—"; });
      if (data.mask !== undefined)    document.querySelectorAll(".net-mask").forEach(el => { el.textContent = data.mask || "—"; });
      if (data.mac !== undefined)     document.querySelectorAll(".net-mac").forEach(el => { el.textContent = data.mac || "—"; });
      if (data.dns !== undefined)     document.querySelectorAll(".net-dns").forEach(el => { el.textContent = data.dns || "—"; });
      if (data.public_ip !== undefined)  document.querySelectorAll(".net-public-ip").forEach(el => { el.textContent = data.public_ip || "—"; });
      if (data.ddns_host !== undefined)  document.querySelectorAll(".net-ddns-host").forEach(el => { el.textContent = data.ddns_host || "—"; });

      // Red tráfico
      const rxEls = document.querySelectorAll(".net-rx");
      const txEls = document.querySelectorAll(".net-tx");
      if (data.net_rx !== undefined) {
        rxEls.forEach(el => {
          el.textContent = data.net_rx;
        });
      }
      if (data.net_tx !== undefined) {
        txEls.forEach(el => {
          el.textContent = data.net_tx;
        });
      }

      // Temperatura / ventilador
      const tempEl = document.getElementById("cpu-temp");
      if (tempEl) tempEl.textContent = data.cpu_temp !== null ? `${data.cpu_temp} ºC` : "—";
      const fanEl = document.getElementById("fan-rpm");
      if (fanEl) fanEl.textContent = data.fan_rpm !== null ? `${data.fan_rpm} rpm` : "—";

      // Disco I/O (MB/s)
      if (data.disk_read !== undefined && data.disk_write !== undefined) {
        const now = Date.now();
        if (window.lastDiskSample) {
          const dt = (now - window.lastDiskSample.t) / 1000;
          if (dt > 0) {
            const readRate = (data.disk_read - window.lastDiskSample.read) / (1024 * 1024) / dt;
            const writeRate = (data.disk_write - window.lastDiskSample.write) / (1024 * 1024) / dt;

            const rxText = document.getElementById("disk-rx");
            const txText = document.getElementById("disk-tx");
            if (rxText) rxText.textContent = `${readRate.toFixed(1)} MB/s`;
            if (txText) txText.textContent = `${writeRate.toFixed(1)} MB/s`;

            const rxBar = document.getElementById("disk-rx-bar");
            const txBar = document.getElementById("disk-tx-bar");
            const scale = 200; // MB/s max for 100%
            if (rxBar) rxBar.style.width = `${Math.min(100, (readRate / scale) * 100)}%`;
            if (txBar) txBar.style.width = `${Math.min(100, (writeRate / scale) * 100)}%`;
            // Disco I/O por dispositivo (usa el sample previo)
            if (data.disk_io && window.lastDiskSample.perdisk) {
              const deviceMap = window.diskDeviceMap || {};
              const ratesByDisk = {};
              const ratesByBase = {};
              Object.keys(data.disk_io).forEach(name => {
                if (name.startsWith("loop") || name.startsWith("ram") || name.startsWith("dm-")) return;
                const prev = window.lastDiskSample.perdisk[name];
                if (!prev) return;
                const r = (data.disk_io[name].read - prev.read) / (1024 * 1024) / dt;
                const w = (data.disk_io[name].write - prev.write) / (1024 * 1024) / dt;
                const base = getDeviceBase(name);
                if (!base) return;

                if (!ratesByBase[base]) ratesByBase[base] = { read: 0, write: 0 };
                ratesByBase[base].read += r;
                ratesByBase[base].write += w;

                const mapEntry = deviceMap[base];
                const diskKey = mapEntry ? mapEntry.disk : base;
                if (!ratesByDisk[diskKey]) {
                  ratesByDisk[diskKey] = { read: 0, write: 0, device: base, label: mapEntry ? mapEntry.label : base };
                }
                ratesByDisk[diskKey].read += r;
                ratesByDisk[diskKey].write += w;
              });

              const history = window.diskIoHistory || {};
              const maxPoints = 40;
              Object.keys(ratesByDisk).forEach(key => {
                if (!history[key]) history[key] = [];
                history[key].push({ r: ratesByDisk[key].read, w: ratesByDisk[key].write });
                if (history[key].length > maxPoints) history[key].shift();
              });
              window.diskIoHistory = history;
              window.lastDiskRatesByDisk = ratesByDisk;
              window.lastDiskRatesByBase = ratesByBase;
            }
          }
        }
        window.lastDiskSample = { t: now, read: data.disk_read, write: data.disk_write, perdisk: data.disk_io || {} };
      }

      // Disco
      const diskInfo = document.getElementById("disk-info");
      const diskBar = document.getElementById("disk-bar");

      if (diskInfo && data.disk_used !== undefined && data.disk_total !== undefined) {
        diskInfo.textContent = `${data.disk_used} / ${data.disk_total} GB`;
      }
      if (diskBar && data.disk_percent !== undefined) {
        diskBar.style.width = data.disk_percent + "%";
      }

      // Servicios
      if (data.services) {
        const lists = document.querySelectorAll(".services-list");
        if (lists.length) {
          lists.forEach(list => renderServices(data.services, list));
        } else {
          renderServices(data.services);
        }
      }
      
      // Seguridad
      if (data.security) {
        const lists = document.querySelectorAll(".security-list");
        if (lists.length) {
          lists.forEach(list => renderSecurity(data.security, list));
        } else {
          renderSecurity(data.security);
        }
      }

      // Top procesos (evita parpadeos si la API devuelve vacío puntualmente)
      const processes = Array.isArray(data.processes) ? data.processes : [];
      if (processes.length) window.lastTopProcesses = processes;
      const stableProcesses = (processes.length ? processes : (window.lastTopProcesses || []));
      renderTopProcesses(stableProcesses, "processes-list");
      renderTopProcesses(stableProcesses, "top-processes-main");
       
      // Discos
      if (data.disks){
         renderDisks(data.disks);
      }
      if (data.disks_tree) {
        const disksTab = document.querySelector('.tabs a[data-tab="disks"].active');
        if (disksTab && typeof renderDisksDetail === "function") {
          renderDisksDetail(data.disks_tree);
        }
      }

      // Alertas
      renderAlerts(Array.isArray(data.alerts) ? data.alerts : []);

   
    });

}

function renderAlerts(alerts) {
  const list = document.getElementById("alerts-list");
  if (!list) return;

  list.innerHTML = "";

  const items = Array.isArray(alerts) ? alerts : [];
  if (!items.length) {
    const item = document.createElement("div");
    item.className = "process-item alert ok";
    item.innerHTML = `
      <span class="process-name">Sistema estable</span>
      <span class="process-stats"></span>
    `;
    list.appendChild(item);
    return;
  }

  items.forEach(a => {
    const item = document.createElement("div");
    item.className = `process-item alert ${a.level}`;

    const name = document.createElement("span");
    name.className = "process-name";
    name.textContent = a.text;

    const age = document.createElement("span");
    age.className = "process-stats";
    age.textContent = a.age || "";

    item.appendChild(name);
    item.appendChild(age);
    list.appendChild(item);
  });
}



function renderDisks(disks) {
  const list = document.getElementById("disks-list");
  if (!list || !Array.isArray(disks)) return;

  list.innerHTML = "";

  disks.forEach(disk => {
    const row = document.createElement("div");
    row.className = "disk-row";

    row.innerHTML = `
      <div class="disk-label">
        <span class="status ${escHtml(disk.status)}"></span> ${escHtml(disk.label)}
      </div>

      <div class="disk-bar">
        <div class="disk-fill ${escHtml(disk.status)}"
             style="width:${escHtml(disk.percent)}%"></div>
      </div>

      <div class="disk-value">
        ${escHtml(disk.used)} / ${escHtml(disk.total)} GB · ${escHtml(disk.percent)}%
      </div>
    `;

    list.appendChild(row);
  });
}



function statusText(status) {
  switch (status) {
    case "ok":   return "Activo";
    case "warn": return "Advertencia";
    case "err":  return "Inactivo";
    default:     return "Desconocido";
  }
}

function renderServices(services, list) {
  const target = list || document.querySelector(".services-list") || document.getElementById("services-list");
  if (!target) return;

  target.innerHTML = "";

  services.forEach(svc => {
    const item = document.createElement("div");
    item.className = "process-item";

    const name = document.createElement("span");
    name.className = "process-name";
    name.textContent = svc.name;

    const stats = document.createElement("span");
    stats.className = "process-stats";

    const led = document.createElement("span");
    led.className = `status ${svc.status}`;

    const text = document.createTextNode(" " + statusText(svc.status));

    stats.appendChild(led);
    stats.appendChild(text);

    item.appendChild(name);
    item.appendChild(stats);

    if (target.closest("#tab-servicios")) {
      item.classList.add("row-interactive");
      item.addEventListener("click", () => {
        target
          .querySelectorAll(".process-item.active")
          .forEach(el => el.classList.remove("active"));
        item.classList.add("active");
        window.lastSelectedServiceName = svc.name;
        updateServicePanel(svc);
      });
      if (window.lastSelectedServiceName === svc.name) {
        item.classList.add("active");
        updateServicePanel(svc);
      }
    }

    target.appendChild(item);
  });
}

function renderSecurity(security, list) {
  const target = list || document.querySelector(".security-list") || document.getElementById("security-list");
  if (!target) return;

  target.innerHTML = "";

  security.forEach(sec => {
    const item = document.createElement("div");
    item.className = "process-item";

    const name = document.createElement("span");
    name.className = "process-name";
    name.textContent = sec.name;

    const stats = document.createElement("span");
    stats.className = "process-stats";

    const led = document.createElement("span");
    led.className = `status ${sec.status}`;

    const text = document.createTextNode(" " + statusText(sec.status));

    stats.appendChild(led);
    stats.appendChild(text);

    item.appendChild(name);
    item.appendChild(stats);

    target.appendChild(item);
  });
}

function updateServicePanel(service) {
  const panel = document.getElementById("service-panel");
  if (panel) panel.classList.remove("hidden");

  const title = document.getElementById("panel-service-name");
  if (title) title.textContent = service.name || "Servicio";

  const statusMap = {
    ok: "Operativo",
    warn: "Advertencia",
    err: "Error"
  };

  const statusEl = document.getElementById("service-status");
  if (statusEl) statusEl.textContent = statusMap[service.status] || "Desconocido";

  const port = document.getElementById("service-port");
  if (port) port.textContent = service.port || "—";

  const proto = document.getElementById("service-proto");
  if (proto) proto.textContent = service.proto || "TCP";

  const restartBtn = document.getElementById("service-restart");
  const stopBtn = document.getElementById("service-stop");
  const logsBtn = document.getElementById("service-logs");
  const enableBtn = document.getElementById("service-enable");
  const disableBtn = document.getElementById("service-disable");
  const isFirewall = service.type === "firewall";
  if (restartBtn) { restartBtn.disabled = isFirewall; restartBtn.classList.toggle("hidden", isFirewall); }
  if (stopBtn) { stopBtn.disabled = isFirewall; stopBtn.classList.toggle("hidden", isFirewall); }
  if (logsBtn) logsBtn.disabled = false;
  if (enableBtn) enableBtn.classList.toggle("hidden", !isFirewall);
  if (disableBtn) disableBtn.classList.toggle("hidden", !isFirewall);

  fetchJSON(`/api/services/${encodeURIComponent(service.name)}`)
    .then(data => {
      if (data.error) return;
      const autostart = document.getElementById("service-autostart");
      if (autostart) autostart.textContent = data.autostart ? "Sí" : "No";

      const iface = document.getElementById("service-iface");
      if (iface) iface.textContent = "—";

      const last = document.getElementById("service-last-restart");
      if (last) last.textContent = data.last_restart || "—";

      const uptime = document.getElementById("service-uptime");
      if (uptime) uptime.textContent = data.uptime || "—";
    });
}

document.addEventListener("DOMContentLoaded", () => {
  const restartBtn = document.getElementById("service-restart");
  const stopBtn = document.getElementById("service-stop");
  const logsBtn = document.getElementById("service-logs");

  if (restartBtn) {
    restartBtn.addEventListener("click", () => {
      const name = window.lastSelectedServiceName;
      if (!name) return;
      fetchJSON(`/api/services/${encodeURIComponent(name)}/restart`, { method: "POST" })
        .then(data => {
          if (data.error) alert(data.error);
          else updateSystem();
        })
        .catch(alertActionError);
    });
  }

  if (stopBtn) {
    stopBtn.addEventListener("click", () => {
      const name = window.lastSelectedServiceName;
      if (!name) return;
      fetchJSON(`/api/services/${encodeURIComponent(name)}/stop`, { method: "POST" })
        .then(data => {
          if (data.error) alert(data.error);
          else updateSystem();
        })
        .catch(alertActionError);
    });
  }

  if (logsBtn) {
    logsBtn.addEventListener("click", () => {
      const logsTab = document.querySelector('.tabs a[data-tab="logs"]');
      if (logsTab) logsTab.click();
      if (typeof renderLogs === "function") {
        const name = window.lastSelectedServiceName || "";
        renderLogs("service", name);
      }
    });
  }

  const enableBtn = document.getElementById("service-enable");
  const disableBtn = document.getElementById("service-disable");

  if (enableBtn) {
    enableBtn.addEventListener("click", () => {
      fetchJSON("/api/services/firewall/enable", { method: "POST" })
        .then(data => { if (data.error) alert(data.error); else updateSystem(); })
        .catch(alertActionError);
    });
  }

  if (disableBtn) {
    disableBtn.addEventListener("click", () => {
      if (!confirm("¿Desactivar el firewall?")) return;
      fetchJSON("/api/services/firewall/disable", { method: "POST" })
        .then(data => { if (data.error) alert(data.error); else updateSystem(); })
        .catch(alertActionError);
    });
  }
});

function renderTopProcesses(processes, targetId = "top-processes") {
  const list = document.getElementById(targetId);
  if (!list) return;

  list.innerHTML = "";

  const items = Array.isArray(processes) ? processes.slice(0, 4) : [];
  while (items.length < 4) {
    items.push({ name: "-", cpu: null, ram: null });
  }

  items.forEach(p => {
    const item = document.createElement("div");
    item.className = "process-item";

    const name = document.createElement("span");
    name.className = "process-name";
    name.textContent = (p && p.name) ? p.name : "-";
    name.title = (p && p.name) ? p.name : "";
    if (p && p.device) {
      const dev = document.createElement("span");
      dev.style.cssText = "color:var(--text-muted);font-size:0.82em;margin-left:6px";
      dev.textContent = "· " + p.device;
      name.appendChild(dev);
      name.title = p.name + " — " + p.device;
    }

    const stats = document.createElement("span");
    stats.className = "process-stats";

    const cpu = document.createElement("span");
    cpu.className = "process-cpu";
    const cpuNum = (p && p.cpu !== null && p.cpu !== undefined && p.cpu !== "") ? Number(p.cpu) : NaN;
    const cpuNorm = Number.isFinite(cpuNum) ? Math.min(100, Math.max(0, cpuNum)) : NaN;
    const cpuVal = Number.isFinite(cpuNorm) ? cpuNorm.toFixed(1) : "-";
    cpu.textContent = `CPU: ${cpuVal}%`;

    const ram = document.createElement("span");
    ram.className = "process-ram";
    const ramNum = (p && p.ram !== null && p.ram !== undefined && p.ram !== "") ? Number(p.ram) : NaN;
    const ramVal = Number.isFinite(ramNum) ? ramNum.toFixed(1) : "-";
    ram.textContent = `RAM: ${ramVal} MB`;

    stats.appendChild(cpu);
    stats.appendChild(ram);

    item.appendChild(name);
    item.appendChild(stats);

    list.appendChild(item);
  });
}

document.addEventListener("DOMContentLoaded", () => {
  // En la pantalla de login no inicializamos refrescos ni tabs.
  if (document.querySelector("form.login-form")) return;
  initTabs();
  updateSystem();
  setInterval(updateSystem, 3000); // cada 3 segundos
  renderSecurityLog();
  renderBackupTasks();
  renderUpdatesList();

  const updatesStatusEl = document.getElementById("updates-action-status");
  const updatesOutEl = document.getElementById("updates-action-output");
  function setUpdatesActionStatus(text, kind = "", output = "") {
    if (updatesStatusEl) {
      updatesStatusEl.classList.remove("ok", "err");
      if (kind) updatesStatusEl.classList.add(kind);
      updatesStatusEl.textContent = text || "-";
    }
    if (updatesOutEl) {
      if (output) {
        updatesOutEl.textContent = output;
        updatesOutEl.classList.remove("hidden");
      } else {
        updatesOutEl.textContent = "";
        updatesOutEl.classList.add("hidden");
      }
    }
  }

  const runBtn = document.getElementById("backup-run");
  if (runBtn) {
    runBtn.addEventListener("click", () => {
      fetch("/api/backups/run", { method: "POST" })
        .then(r => {
          if (r.status === 401) {
            window.location.href = "/login";
            throw new Error("unauthorized");
          }
          return r.json();
        })
        .then(data => {
          if (data.error) {
            alert(data.error);
            return;
          }
          renderBackupTasks();
        });
    });
  }

  const updatesRun = document.getElementById("updates-run");
  const updatesRestartPanel = document.getElementById("updates-restart-panel");
  if (updatesRun) {
    updatesRun.addEventListener("click", () => {
      if (!confirm("¿Aplicar actualizaciones ahora?")) return;
      updatesRun.disabled = true;
      if (updatesReboot) updatesReboot.disabled = true;
      if (updatesRestartPanel) updatesRestartPanel.disabled = true;
      setUpdatesActionStatus("Aplicando actualizaciones...", "", "");
      fetchJSON("/api/updates/run", { method: "POST" })
        .then(data => {
          if (data.error) {
            setUpdatesActionStatus("Error aplicando actualizaciones", "err", String(data.error || ""));
            alert(data.error);
            return;
          }
          setUpdatesActionStatus("Actualizaciones aplicadas", "ok", String(data.output || ""));
          renderUpdatesList();
        })
        .catch(err => {
          setUpdatesActionStatus("Error aplicando actualizaciones", "err", String(err || ""));
          alert(err);
        })
        .finally(() => {
          updatesRun.disabled = false;
          if (updatesReboot) updatesReboot.disabled = false;
          if (updatesRestartPanel) updatesRestartPanel.disabled = false;
        });
    });
  }

  const updatesReboot = document.getElementById("updates-reboot");
  if (updatesRestartPanel) {
    updatesRestartPanel.addEventListener("click", () => {
      if (!confirm("??Reiniciar solo el panel ahora?")) return;
      updatesRestartPanel.disabled = true;
      if (updatesRun) updatesRun.disabled = true;
      if (updatesReboot) updatesReboot.disabled = true;
      setUpdatesActionStatus("Reiniciando panel...", "", "");
      // Captura el PID del proceso ACTUAL antes de pedir el reinicio: el
      // overlay lo usara para distinguir un 200 OK real del panel nuevo de
      // uno que en realidad viene del proceso viejo, que sigue vivo un
      // instante despues del click (ver _showPanelRestartOverlay).
      fetch("/api/system/ping", { credentials: "same-origin" })
        .then(r => (r.ok ? r.json() : null))
        .catch(() => null)
        .then(baseline => {
          const basePid = baseline ? baseline.pid : null;
          fetchJSON("/api/system/restart-panel", { method: "POST" })
            .then(data => {
              if (data.error) {
                setUpdatesActionStatus("Error reiniciando panel", "err", String(data.error || ""));
                alert(data.error);
                updatesRestartPanel.disabled = false;
                if (updatesRun) updatesRun.disabled = false;
                if (updatesReboot) updatesReboot.disabled = false;
                return;
              }
              _showPanelRestartOverlay(basePid);
            })
            .catch(err => {
              const m = String((err && err.message) || err || "");
              if (/^HTTP 4\d\d/.test(m)) {
                // El servidor contestó con un error (p.ej. 403 por rol): NO se está reiniciando nada
                setUpdatesActionStatus("No se pudo reiniciar el panel", "err", "");
                alertActionError(err);
                updatesRestartPanel.disabled = false;
                if (updatesRun) updatesRun.disabled = false;
                if (updatesReboot) updatesReboot.disabled = false;
                return;
              }
              // El servidor puede cortar la conexión antes de responder — es normal
              _showPanelRestartOverlay(basePid);
            });
        });
    });
  }

  if (updatesReboot) {
    updatesReboot.addEventListener("click", () => {
      if (!confirm("¿Reiniciar el sistema ahora?\n\nEl panel quedará inaccesible hasta que el NAS arranque de nuevo.")) return;
      updatesReboot.disabled = true;
      if (updatesRun) updatesRun.disabled = true;
      if (updatesRestartPanel) updatesRestartPanel.disabled = true;
      setUpdatesActionStatus("Solicitando reinicio...", "", "");
      fetchJSON("/api/system/reboot", { method: "POST" })
        .then(data => {
          if (data.error) {
            setUpdatesActionStatus("Error solicitando reinicio", "err", String(data.error || ""));
            alert(data.error);
            updatesReboot.disabled = false;
            if (updatesRun) updatesRun.disabled = false;
            if (updatesRestartPanel) updatesRestartPanel.disabled = false;
            return;
          }
          // Mostrar overlay de reinicio y reconectar automáticamente
          _showRebootOverlay();
        })
        .catch(() => {
          // El servidor puede cortar la conexión antes de responder — es normal
          _showRebootOverlay();
        });
    });
  }

  const updatesShutdown = document.getElementById('updates-shutdown');
  if (updatesShutdown) {
    updatesShutdown.addEventListener('click', () => {
      if (!confirm('Apagar el NAS ahora. Tendras que encenderlo manualmente.')) return;
      updatesShutdown.disabled = true;
      if (updatesRun) updatesRun.disabled = true;
      if (updatesRestartPanel) updatesRestartPanel.disabled = true;
      if (updatesReboot) updatesReboot.disabled = true;
      setUpdatesActionStatus('Apagando NAS...', '', '');
      fetchJSON('/api/system/shutdown', { method: 'POST' })
        .then(data => {
          if (data && data.error) {
            setUpdatesActionStatus('Error apagando NAS', 'err', String(data.error || ''));
            alert(data.error);
            updatesShutdown.disabled = false;
            if (updatesRun) updatesRun.disabled = false;
            if (updatesRestartPanel) updatesRestartPanel.disabled = false;
            if (updatesReboot) updatesReboot.disabled = false;
            return;
          }
          setUpdatesActionStatus('NAS apagandose...', 'ok', 'El NAS se apagara en unos segundos.');
        })
        .catch(() => {
          setUpdatesActionStatus('NAS apagandose...', 'ok', 'El NAS se apagara en unos segundos.');
        });
    });
  }
});


// ---- Apagado programado ----
function loadPowerStatus() {
  fetch("/api/power/status").then(r => r.json()).then(d => {
    const el = id => document.getElementById(id);
    if (el("power-enabled-label"))   el("power-enabled-label").textContent   = d.enabled ? "Activo" : "Desactivado";
    if (el("power-next-shutdown"))   el("power-next-shutdown").textContent   = d.next_shutdown || "—";
    if (el("power-shutdown-time"))   el("power-shutdown-time").value          = d.shutdown_time || "02:00";
    if (el("power-days") && d.days)  el("power-days").value                   = d.days;
  }).catch(() => {});
}

function savePowerSchedule(enabled) {
  const el = id => document.getElementById(id);
  const payload = {
    enabled: enabled,
    shutdown_time: el("power-shutdown-time") ? el("power-shutdown-time").value : "02:00",
    days:          el("power-days")           ? el("power-days").value           : "all",
  };
  fetch("/api/power/schedule", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload) })
    .then(r => r.json()).then(d => {
      if (d.ok) { loadPowerStatus(); alert(enabled ? "Apagado programado activado." : "Apagado programado desactivado."); }
      else alert(d.error || "Error");
    }).catch(e => alert(e));
}

document.addEventListener("DOMContentLoaded", () => {
  loadPowerStatus();
  const btnEnable  = document.getElementById("power-enable");
  const btnDisable = document.getElementById("power-disable");
  if (btnEnable)  btnEnable.addEventListener("click",  () => savePowerSchedule(true));
  if (btnDisable) btnDisable.addEventListener("click", () => savePowerSchedule(false));
});
function _showRebootOverlay() {
  // Parar el poller principal
  if (window._mainPollTimer) clearInterval(window._mainPollTimer);
  if (window._netPollStarted) window._netPollStarted = false;

  const overlay = document.createElement("div");
  overlay.id = "reboot-overlay";
  overlay.style.cssText = `
    position:fixed; inset:0; z-index:9999;
    background:rgba(10,14,22,0.97);
    display:flex; flex-direction:column;
    align-items:center; justify-content:center; gap:20px;
    font-family:inherit; color:#e0e6f0;
  `;
  overlay.innerHTML = `
    <div style="font-size:48px;">&#x1F504;</div>
    <div style="font-size:22px; font-weight:600;">Reiniciando NAS...</div>
    <div style="font-size:14px; color:#7a8fa6;" id="reboot-overlay-msg">Esperando que el sistema arranque de nuevo</div>
    <div style="width:220px; height:4px; background:#1e2736; border-radius:2px; overflow:hidden; margin-top:8px;">
      <div id="reboot-overlay-bar" style="height:100%; width:0%; background:#4f8cff; border-radius:2px; transition:width 0.4s;"></div>
    </div>
  `;
  document.body.appendChild(overlay);

  let elapsed = 0;
  const maxWait = 120; // segundos máximo esperando
  const pollInterval = 3000;

  const bar = document.getElementById("reboot-overlay-bar");
  const msg = document.getElementById("reboot-overlay-msg");

  const timer = setInterval(() => {
    elapsed += pollInterval / 1000;
    if (bar) bar.style.width = Math.min((elapsed / maxWait) * 100, 95) + "%";

    fetch("/api/system", { credentials: "same-origin" })
      .then(r => {
        if (r.ok) {
          clearInterval(timer);
          if (bar) bar.style.width = "100%";
          if (msg) msg.textContent = "El NAS ha vuelto. Recargando...";
          setTimeout(() => window.location.reload(), 1200);
        }
      })
      .catch(() => {
        if (msg) msg.textContent = `Esperando... (${Math.round(elapsed)}s)`;
      });

    if (elapsed >= maxWait) {
      clearInterval(timer);
      if (msg) msg.textContent = "Tiempo de espera agotado. Recarga la página manualmente.";
    }
  }, pollInterval);
}

function _showPanelRestartOverlay(basePid) {
  if (window._mainPollTimer) clearInterval(window._mainPollTimer);
  if (window._netPollStarted) window._netPollStarted = false;

  const overlay = document.createElement("div");
  overlay.id = "panel-restart-overlay";
  overlay.style.cssText = `
    position:fixed; inset:0; z-index:9999;
    background:rgba(10,14,22,0.97);
    display:flex; flex-direction:column;
    align-items:center; justify-content:center; gap:20px;
    font-family:inherit; color:#e0e6f0;
  `;
  overlay.innerHTML = `
    <div style="font-size:48px;">&#x1F504;</div>
    <div style="font-size:22px; font-weight:600;">Actualizando panel...</div>
    <div style="font-size:14px; color:#7a8fa6;" id="panel-restart-overlay-msg">Esperando que el panel vuelva a estar disponible</div>
    <div style="width:220px; height:4px; background:#1e2736; border-radius:2px; overflow:hidden; margin-top:8px;">
      <div id="panel-restart-overlay-bar" style="height:100%; width:0%; background:#4f8cff; border-radius:2px; transition:width 0.4s;"></div>
    </div>
  `;
  document.body.appendChild(overlay);

  let elapsed = 0;
  const maxWait = 60;
  const pollInterval = 2000;

  const bar = document.getElementById("panel-restart-overlay-bar");
  const msg = document.getElementById("panel-restart-overlay-msg");

  const timer = setInterval(() => {
    elapsed += pollInterval / 1000;
    if (bar) bar.style.width = Math.min((elapsed / maxWait) * 100, 95) + "%";

    fetch("/api/system/ping", { credentials: "same-origin" })
      .then(r => (r.ok ? r.json() : null))
      .then(data => {
        if (!data) return; // sin respuesta valida todavia, seguir esperando
        // Si conocemos el PID de antes del reinicio, un 200 OK con el MISMO
        // PID es el proceso viejo respondiendo en su ultimo instante de vida
        // (ver el fetch a /api/system/ping antes de lanzar el reinicio) —
        // no cuenta como "ya volvio", hay que seguir esperando al PID nuevo.
        if (basePid != null && data.pid === basePid) {
          if (msg) msg.textContent = `Parando el proceso anterior... (${Math.round(elapsed)}s)`;
          return;
        }
        clearInterval(timer);
        if (bar) bar.style.width = "100%";
        if (msg) msg.textContent = "El panel ha vuelto. Recargando...";
        setTimeout(() => window.location.reload(), 1200);
      })
      .catch(() => {
        if (msg) msg.textContent = `Esperando... (${Math.round(elapsed)}s)`;
      });

    if (elapsed >= maxWait) {
      clearInterval(timer);
      if (msg) msg.textContent = "Tiempo de espera agotado. Recarga la página manualmente.";
    }
  }, pollInterval);
}

function renderSecurityLog() {
  const list = document.getElementById("security-log-list");
  if (!list) return;
  if (window.IX2_ROLE === "viewer") {  // /api/logs exige operator: no se pide
    list.innerHTML = '<div class="process-item">Solo para operator o admin</div>';
    return;
  }

  fetchJSON("/api/logs?filter=error&limit=50")
    .then(data => {
      if (data.error) {
        list.innerHTML = `<div class="process-item">Error: ${escHtml(data.error)}</div>`;
        return;
      }
      const items = Array.isArray(data.items) ? data.items : [];
      list.innerHTML = "";
      items.forEach(ev => {
        const item = document.createElement("div");
        item.className = "process-item row-interactive";
        item.innerHTML = `
          <span class="process-name">${escHtml(ev.title)}</span>
          <span class="process-stats">${escHtml(ev.detail)}</span>
        `;
        item.addEventListener("click", () => {
          list
            .querySelectorAll(".process-item.active")
            .forEach(el => el.classList.remove("active"));
          item.classList.add("active");
          updateSecurityPanel(ev);
        });
        list.appendChild(item);
      });
    })
    .catch(err => {
      list.innerHTML = `<div class="process-item">Error: ${escHtml(err)}</div>`;
    });
}

function updateSecurityPanel(ev) {
  const type = document.getElementById("security-type");
  const origin = document.getElementById("security-origin");
  const result = document.getElementById("security-result");
  const date = document.getElementById("security-date");
  const message = document.getElementById("security-message");

  if (type) type.textContent = ev.type || "—";
  if (origin) origin.textContent = ev.source || "—";
  if (result) result.textContent = ev.title || "—";
  if (date) date.textContent = ev.date || "—";
  if (message) message.textContent = ev.message || "—";
}

function renderBackupTasks() {
  const list = document.getElementById("backup-tasks");
  if (!list) return;

  fetchJSON("/api/backups")
    .then(data => {
      if (data.error) {
        list.innerHTML = `<div class="process-item">Error: ${escHtml(data.error)}</div>`;
        return;
      }
      // Reutiliza el renderizador de backups.js (detalle largo plegable, sin innerHTML con datos)
      if (typeof renderTasks === "function") {
        renderTasks(data);
      } else {
        const tasks = Array.isArray(data.tasks) ? data.tasks : [];
        list.innerHTML = "";
        tasks.forEach(t => {
          const item = document.createElement("div");
          item.className = "process-item";
          item.innerHTML = `
          <span class="process-name">${escHtml(t.name)}</span>
          <span class="process-stats">${escHtml(t.detail)}</span>
        `;
          list.appendChild(item);
        });
      }

      const statusEl = document.getElementById("backup-status");
      if (statusEl) {
        statusEl.innerHTML = data.status === "ok"
          ? '<span class="status ok"></span> OK'
          : '<span class="status warn"></span> Advertencia';
      }
      const lastEl = document.getElementById("backup-last");
      if (lastEl) lastEl.textContent = data.last_backup || "—";
      const dstEl = document.getElementById("backup-dst");
      if (dstEl) dstEl.textContent = data.destination || "—";
      const freeEl = document.getElementById("backup-free");
      if (freeEl) freeEl.textContent = data.free_space || "—";
    })
    .catch(err => {
      list.innerHTML = `<div class="process-item">Error: ${escHtml(err)}</div>`;
    });
}

function renderUpdatesList() {
  const list = document.getElementById("updates-list");
  if (!list) return;

  fetchJSON("/api/updates")
    .then(data => {
      if (data.error) {
        list.innerHTML = `<div class="process-item">Error: ${escHtml(data.error)}</div>`;
        return;
      }
      const updates = Array.isArray(data.items) ? data.items : [];
      list.innerHTML = "";
      updates.forEach(u => {
        const item = document.createElement("div");
        item.className = "process-item row-interactive";
        item.innerHTML = `
          <span class="process-name">${escHtml(u.name)}</span>
          <span class="process-stats">${escHtml(u.detail)}</span>
        `;
        item.addEventListener("click", () => {
          list
            .querySelectorAll(".process-item.active")
            .forEach(el => el.classList.remove("active"));
          item.classList.add("active");
          updateUpdatesPanel(u);
        });
        list.appendChild(item);
      });

      // Si hay pendientes, preselecciona el primero para rellenar el panel lateral.
      if (updates.length) {
        const first = list.querySelector(".process-item");
        if (first) {
          first.classList.add("active");
          updateUpdatesPanel(updates[0]);
        }
      }

      const countEl = document.getElementById("updates-count");
      const count = (data.count ?? updates.length);
      if (countEl) countEl.textContent = count;

      const lastCheck = document.getElementById("updates-last-check");
      if (lastCheck) lastCheck.textContent = data.last_check || "—";

      const state = document.getElementById("updates-state");
      if (state) {
        state.innerHTML = updates.length
          ? '<span class="status warn"></span> Pendientes (' + count + ')'
          : '<span class="status ok"></span> Al día';
      }

      const dashFw = document.getElementById("dash-fw");
      if (dashFw) dashFw.textContent = "—";
      const dashLast = document.getElementById("dash-upd-last");
      const fw = data.firmware || "-";
      const updatesFw = document.getElementById("updates-fw");
      if (updatesFw) updatesFw.textContent = fw;
      const dashFwEl = document.getElementById("dash-fw");
      if (dashFwEl) dashFwEl.textContent = fw;
      const dashCount = document.getElementById("dash-upd-count");
      if (dashCount) dashCount.textContent = count;
      if (dashLast) dashLast.textContent = data.last_check || "—";
      const dashState = document.getElementById("dash-upd-state");
      if (dashState) {
        dashState.innerHTML = updates.length
          ? '<span class="status warn"></span> Pendientes (' + count + ')'
          : '<span class="status ok"></span> Al día';
      }
    })
    .catch(err => {
      list.innerHTML = `<div class="process-item">Error: ${escHtml(err)}</div>`;
    });
}

function updateUpdatesPanel(update) {
  const panel = document.getElementById("updates-panel");
  if (panel) panel.classList.remove("hidden");

  const title = document.getElementById("update-panel-title");
  if (title) title.textContent = update.name;

  const name = document.getElementById("update-name");
  if (name) name.textContent = update.name;

  const version = document.getElementById("update-version");
  if (version) version.textContent = update.version || "—";

  const size = document.getElementById("update-size");
  if (size) size.textContent = update.size || "—";
}


// Inicializar sistema de pestañas
function initTabs() {
  const tabs = document.querySelectorAll(".tabs a[data-tab]");
  const contents = document.querySelectorAll(".tab-content");

  tabs.forEach(tab => {
    tab.addEventListener("click", (e) => {
      e.preventDefault();

      const target = tab.dataset.tab;
      const current = document.querySelector(".tabs a[data-tab].active");
      const currentTab = current ? current.dataset.tab : "";

      if (currentTab === "disks" && target !== "disks") {
        window.lastSelectedPartitionKey = null;
        document
          .querySelectorAll("#tab-disks .partition-row.active")
          .forEach(el => el.classList.remove("active"));
        const panel = document.getElementById("partition-panel");
        if (panel) panel.classList.add("hidden");
      }

      if (currentTab === "wireguard" && target !== "wireguard" && typeof stopWireguardTab === "function") {
        stopWireguardTab();
      }

      if (currentTab === "terminal" && target !== "terminal" && typeof stopTerminalTab === "function") {
        stopTerminalTab();
      }

      // activar tab
      tabs.forEach(t => t.classList.remove("active"));
      tab.classList.add("active");

      // ocultar contenidos
      contents.forEach(c => c.classList.add("hidden"));

      const panel = document.getElementById(`tab-${target}`);
      if (panel) panel.classList.remove("hidden");

      // 🔴 AQUÍ
      if (target === "disks" && window.lastSystemData) {
        renderDisksDetail(window.lastSystemData.disks_tree);
      }

      if (target === "usuarios" && typeof renderUsers === "function") {
        renderUsers();
      }

      if (target === "grupos" && typeof renderGroups === "function") {
        renderGroups();
      }

      if (target === "red" && typeof initRedTab === "function") {
        initRedTab();
      }
      if (target === "red") {
        loadWolTargets();
      }

      if (target === "servicios") {
        loadFail2ban();
      }

      if (target === "backups" && typeof renderBackupTasks === "function") {
        renderBackupTasks();
      }

      if (target === "logs" && typeof renderLogs === "function") {
        renderLogs();
      }

      if (target === "updates" && typeof renderUpdatesList === "function") {
        renderUpdatesList();
      }

      if (target === "wireguard" && typeof initWireguardTab === "function") {
        initWireguardTab();
      }

      if (target === "terminal" && typeof initTerminalTab === "function") {
        initTerminalTab();
      }

    });

  });
}

// ===================== WAKE-ON-LAN =====================
function loadWolTargets() {
  if (window.IX2_ROLE === "viewer") return;  // /api/wol exige operator
  fetch('/api/wol').then(r => r.json()).then(targets => {
    const list = document.getElementById('wol-targets-list');
    if (!list) return;
    if (!targets.length) {
      list.innerHTML = '<div style="color:var(--text-muted);font-size:0.85em;padding:6px 0">Sin equipos configurados</div>';
      return;
    }
    list.innerHTML = '';
    targets.forEach(t => {
      list.appendChild(_wolBuildRow(t));
    });
  }).catch(() => {});
}

function _wolBuildRow(t) {
  const mac = t.mac || '';
  const name = t.name || '';
  const ip = t.ip || '';

  const row = document.createElement('div');
  row.className = 'process-item';

  const nameSpan = document.createElement('span');
  nameSpan.className = 'process-name';
  nameSpan.textContent = name;
  if (ip) {
    const ipSpan = document.createElement('span');
    ipSpan.style.cssText = 'color:var(--text-muted);font-size:0.82em;margin-left:4px';
    ipSpan.textContent = ip;
    nameSpan.appendChild(ipSpan);
  }

  const btns = document.createElement('span');
  btns.className = 'process-stats';
  btns.style.cssText = 'display:flex;gap:6px;';

  const btnOn = document.createElement('button');
  btnOn.className = 'btn btn-sm';
  btnOn.textContent = 'Encender';
  btnOn.addEventListener('click', function() { wolSend(mac, name, btnOn); });

  btns.appendChild(btnOn);

  if (ip) {
    const btnOff = document.createElement('button');
    btnOff.className = 'btn btn-sm';
    btnOff.style.background = 'var(--warn-muted,#5a4000)';
    btnOff.textContent = 'Apagar';
    btnOff.addEventListener('click', function() { wolShutdown(name, btnOff); });
    btns.appendChild(btnOff);
  }

  const btnEdit = document.createElement('button');
  btnEdit.className = 'btn btn-sm';
  btnEdit.textContent = 'Editar';
  btnEdit.addEventListener('click', function() { row.replaceWith(_wolBuildEditRow(t)); });
  btns.appendChild(btnEdit);

  const btnDel = document.createElement('button');
  btnDel.className = 'btn btn-sm btn-danger';
  btnDel.style.marginLeft = '12px';
  btnDel.textContent = 'Borrar';
  btnDel.addEventListener('click', function() { wolDeleteTarget(name, btnDel); });

  btns.appendChild(btnDel);
  row.appendChild(nameSpan);
  row.appendChild(btns);
  return row;
}

function _wolBuildEditRow(t) {
  const oldName = t.name || '';

  const row = document.createElement('div');
  row.className = 'process-item';
  row.style.cssText = 'flex-wrap:wrap;gap:6px;align-items:center;';

  const inName = document.createElement('input');
  inName.type = 'text';
  inName.value = oldName;
  inName.placeholder = 'Nombre';
  inName.style.cssText = 'flex:1;min-width:90px;';

  const inMac = document.createElement('input');
  inMac.type = 'text';
  inMac.value = t.mac || '';
  inMac.placeholder = 'MAC (aa:bb:cc:dd:ee:ff)';
  inMac.style.cssText = 'flex:2;min-width:160px;';

  const inIp = document.createElement('input');
  inIp.type = 'text';
  inIp.value = t.ip || '';
  inIp.placeholder = 'IP (opcional)';
  inIp.style.cssText = 'flex:1;min-width:100px;';

  const btns = document.createElement('span');
  btns.style.cssText = 'display:flex;gap:6px;';

  const btnSave = document.createElement('button');
  btnSave.className = 'btn btn-sm';
  btnSave.textContent = 'Guardar';
  btnSave.addEventListener('click', function() {
    wolSaveEdit(oldName, inName.value, inMac.value, inIp.value, row, btnSave);
  });

  const btnCancel = document.createElement('button');
  btnCancel.className = 'btn btn-sm';
  btnCancel.textContent = 'Cancelar';
  btnCancel.addEventListener('click', function() { row.replaceWith(_wolBuildRow(t)); });

  btns.appendChild(btnSave);
  btns.appendChild(btnCancel);
  row.appendChild(inName);
  row.appendChild(inMac);
  row.appendChild(inIp);
  row.appendChild(btns);
  return row;
}

function wolSaveEdit(oldName, name, mac, ip, row, btn) {
  const msgEl = document.getElementById('wol-msg');
  if (!name.trim() || !mac.trim()) {
    if (msgEl) { msgEl.textContent = 'Nombre y MAC son obligatorios'; msgEl.style.color = 'var(--err, red)'; }
    return;
  }
  if (btn) btn.disabled = true;
  fetch('/api/wol/targets/edit', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({old_name: oldName, name: name.trim(), mac: mac.trim(), ip: ip.trim()})
  })
  .then(r => r.json())
  .then(data => {
    if (data.ok) {
      if (msgEl) { msgEl.textContent = 'Equipo actualizado'; msgEl.style.color = 'var(--ok, green)'; }
      loadWolTargets();
    } else {
      if (msgEl) { msgEl.textContent = data.error || 'Error'; msgEl.style.color = 'var(--err, red)'; }
      if (btn) btn.disabled = false;
    }
  })
  .catch(e => {
    if (msgEl) { msgEl.textContent = 'Error de red: ' + e; msgEl.style.color = 'red'; }
    if (btn) btn.disabled = false;
  });
}

function wolSend(mac, name, btn) {
  const msgEl = document.getElementById('wol-msg');
  if (btn) btn.disabled = true;
  fetch('/api/wol', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({mac: mac})
  })
  .then(r => r.json())
  .then(data => {
    if (msgEl) {
      if (data.ok) {
        msgEl.textContent = 'Paquete WoL enviado a ' + name + ' (' + mac + ')';
        msgEl.style.color = 'var(--ok, green)';
      } else {
        msgEl.textContent = 'Error: ' + (data.error || 'desconocido');
        msgEl.style.color = 'var(--err, red)';
      }
    }
    if (btn) setTimeout(() => { btn.disabled = false; }, 3000);
  })
  .catch(e => {
    if (msgEl) { msgEl.textContent = 'Error de red: ' + e; msgEl.style.color = 'red'; }
    if (btn) btn.disabled = false;
  });
}

function wolAddTarget() {
  const name = (document.getElementById('wol-new-name') || {}).value || '';
  const mac  = (document.getElementById('wol-new-mac')  || {}).value || '';
  const ip   = (document.getElementById('wol-new-ip')   || {}).value || '';
  const msg  = document.getElementById('wol-add-msg');
  if (!name.trim() || !mac.trim()) {
    if (msg) { msg.textContent = 'Nombre y MAC son obligatorios'; msg.style.color = 'var(--err, red)'; }
    return;
  }
  fetch('/api/wol/targets', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: name.trim(), mac: mac.trim(), ip: ip.trim()})
  })
  .then(r => r.json())
  .then(data => {
    if (data.ok) {
      if (msg) { msg.textContent = 'Equipo añadido'; msg.style.color = 'var(--ok, green)'; }
      document.getElementById('wol-new-name').value = '';
      document.getElementById('wol-new-mac').value = '';
      document.getElementById('wol-new-ip').value = '';
      loadWolTargets();
    } else {
      if (msg) { msg.textContent = data.error || 'Error'; msg.style.color = 'var(--err, red)'; }
    }
  })
  .catch(e => { if (msg) { msg.textContent = 'Error: ' + e; msg.style.color = 'red'; } });
}

function wolShutdown(name, btn) {
  if (!confirm('¿Apagar ' + name + '?')) return;
  if (btn) btn.disabled = true;
  const msgEl = document.getElementById('wol-msg');
  fetch('/api/wol/shutdown', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: name})
  })
  .then(r => r.json())
  .then(data => {
    if (msgEl) {
      if (data.ok) {
        msgEl.textContent = name + ' se apagará en 5 segundos';
        msgEl.style.color = 'var(--ok, green)';
      } else {
        msgEl.textContent = 'Error: ' + (data.error || 'desconocido');
        msgEl.style.color = 'var(--err, red)';
      }
    }
    if (btn) setTimeout(() => { btn.disabled = false; }, 5000);
  })
  .catch(e => {
    if (msgEl) { msgEl.textContent = 'Error: ' + e; msgEl.style.color = 'red'; }
    if (btn) btn.disabled = false;
  });
}

function wolDeleteTarget(name, btn) {
  if (!confirm('¿Borrar ' + name + '?')) return;
  if (btn) btn.disabled = true;
  fetch('/api/wol/targets/delete', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: name})
  })
  .then(r => r.json())
  .then(data => {
    const msg = document.getElementById('wol-msg');
    if (data.ok) {
      loadWolTargets();
    } else {
      if (msg) { msg.textContent = data.error || 'Error'; msg.style.color = 'var(--err, red)'; }
      if (btn) btn.disabled = false;
    }
  })
  .catch(() => { if (btn) btn.disabled = false; });
}

// ===================== FAIL2BAN =====================
function loadFail2ban() {
  if (window.IX2_ROLE === "viewer") return;  // /api/fail2ban exige operator
  const listEl = document.getElementById('fail2ban-jails-list');
  const msgEl = document.getElementById('fail2ban-msg');
  if (!listEl) return;
  listEl.innerHTML = '<div class="process-item"><span class="process-stats">Cargando...</span></div>';
  fetch('/api/fail2ban')
    .then(r => r.json())
    .then(data => {
      listEl.innerHTML = '';
      if (data.error) {
        listEl.innerHTML = '<div class="process-item"><span class="process-stats">' + escHtml(data.error) + '</span></div>';
        return;
      }
      if (!data.jails || data.jails.length === 0) {
        listEl.innerHTML = '<div class="process-item"><span class="process-stats">No hay jails activas</span></div>';
        return;
      }
      data.jails.forEach(jail => {
        const header = document.createElement('div');
        header.className = 'process-item';
        header.innerHTML = '<span class="process-name"><strong>' + escHtml(jail.name) + '</strong></span>' +
          '<span class="process-stats">Baneadas ahora: <b>' + escHtml(jail.currently_banned) + '</b> / Total: <b>' + escHtml(jail.total_banned) + '</b></span>';
        listEl.appendChild(header);
        if (jail.banned_ips && jail.banned_ips.length > 0) {
          jail.banned_ips.forEach(ip => {
            const row = document.createElement('div');
            row.className = 'process-item';
            row.style.paddingLeft = '1rem';
            const ipSpan = document.createElement('span');
            ipSpan.className = 'process-name monospace';
            ipSpan.style.fontSize = '0.85em';
            ipSpan.textContent = ip;
            const unbanBtn = document.createElement('button');
            unbanBtn.className = 'btn btn-sm btn-danger';
            unbanBtn.textContent = 'Desbanear';
            unbanBtn.onclick = () => fail2banUnban(jail.name, ip, unbanBtn);
            const stats = document.createElement('span');
            stats.className = 'process-stats';
            stats.appendChild(unbanBtn);
            row.appendChild(ipSpan);
            row.appendChild(stats);
            listEl.appendChild(row);
          });
        }
      });
    })
    .catch(e => {
      if (listEl) listEl.innerHTML = '<div class="process-item"><span class="process-stats">Error: ' + escHtml(e) + '</span></div>';
    });
}

function fail2banUnban(jail, ip, btn) {
  const msgEl = document.getElementById('fail2ban-msg');
  if (btn) btn.disabled = true;
  fetch('/api/fail2ban/unban', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({jail: jail, ip: ip})
  })
  .then(r => r.json())
  .then(data => {
    if (msgEl) {
      if (data.ok) {
        msgEl.textContent = ip + ' desbaneada de ' + jail;
        msgEl.style.color = 'var(--ok, green)';
      } else {
        msgEl.textContent = 'Error: ' + (data.error || data.output || 'desconocido');
        msgEl.style.color = 'var(--err, red)';
      }
    }
    setTimeout(() => loadFail2ban(), 1000);
  })
  .catch(e => {
    if (msgEl) { msgEl.textContent = 'Error de red: ' + e; msgEl.style.color = 'red'; }
    if (btn) btn.disabled = false;
  });
}

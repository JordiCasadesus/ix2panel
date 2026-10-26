// =====================================================
// WireGuard tab – UI logic
// =====================================================

let _wgState = null;         // last fetched status
let _wgSelectedPubkey = null; // pubkey of peer being edited (null = new)

// Returns true if the handshake string (from `wg show`) is within the last 3 minutes.
// WireGuard renegotiates every ~180s; older = disconnected.
function wgHandshakeIsRecent(hs) {
  if (!hs || hs === "0" || hs.toLowerCase().includes("never")) return false;
  let secs = 0;
  const h = hs.match(/(\d+)\s*hour/);
  const m = hs.match(/(\d+)\s*minute/);
  const s = hs.match(/(\d+)\s*second/);
  if (h) secs += parseInt(h[1]) * 3600;
  if (m) secs += parseInt(m[1]) * 60;
  if (s) secs += parseInt(s[1]);
  // If no time components matched, assume it's present but unknown format → show green
  if (!h && !m && !s) return true;
  return secs < 300; // 5 minutes threshold
}

// -------------------------------------------------------
// Fetch helpers
// -------------------------------------------------------
function wgFetch(url, opts) {
  return fetch(url, opts).then(r => r.json());
}

// -------------------------------------------------------
// Render summary card (left panel top)
// -------------------------------------------------------
function renderWgStatus(data) {
  const statusEl  = document.getElementById("wg-status-val");
  const endpointEl = document.getElementById("wg-endpoint-val");
  const pubkeyEl  = document.getElementById("wg-pubkey-val");
  const countEl   = document.getElementById("wg-peers-count");

  if (!statusEl) return;

  if (data.running) {
    statusEl.innerHTML = `<span class="status ok"></span> Activo`;
  } else {
    statusEl.innerHTML = `<span class="status err"></span> Inactivo`;
  }

  endpointEl.textContent = data.endpoint || "-";
  pubkeyEl.textContent   = data.server_pubkey || "-";
  countEl.textContent    = String((data.peers || []).length);
}

// -------------------------------------------------------
// Render peers list (left panel)
// -------------------------------------------------------
function renderWgPeersList(peers) {
  const list = document.getElementById("wg-peers-list");
  if (!list) return;
  list.innerHTML = "";

  if (!peers || peers.length === 0) {
    list.innerHTML = `<div class="process-row"><span class="proc-name" style="color:var(--text-muted);font-size:12px;">Sin dispositivos</span></div>`;
    return;
  }

  peers.forEach(p => {
    const row = document.createElement("div");
    row.className = "process-row row-interactive";
    if (_wgSelectedPubkey === p.pubkey) row.classList.add("active");

    const name    = p.name || p.pubkey.slice(0, 12) + "…";
    const ip      = p.allowed_ips ? p.allowed_ips.replace("/32", "") : "-";
    const hs      = p.handshake || "";
    const online  = wgHandshakeIsRecent(hs);
    const dot     = online ? `<span class="status ok"></span>` : `<span class="status err"></span>`;

    row.innerHTML = `
      <div class="proc-name">${dot} ${escHtml(name)}</div>
      <div class="proc-meta">${escHtml(ip)}${hs ? " · " + escHtml(hs) : ""}</div>
    `;
    row.addEventListener("click", () => openPeerEditor(p));
    list.appendChild(row);
  });
}

// -------------------------------------------------------
// Panel visibility helpers
// -------------------------------------------------------
function showPanelSection(id) {
  ["wg-init-section", "wg-peer-section", "wg-server-section"].forEach(sid => {
    const el = document.getElementById(sid);
    if (el) el.classList.toggle("hidden", sid !== id);
  });
}

// -------------------------------------------------------
// Open peer editor (right panel)
// -------------------------------------------------------
function openPeerEditor(peer) {
  _wgSelectedPubkey = peer ? peer.pubkey : null;

  // Highlight row
  document.querySelectorAll("#wg-peers-list .process-row").forEach(r => r.classList.remove("active"));
  if (peer) {
    document.querySelectorAll("#wg-peers-list .process-row").forEach(r => {
      if (r.querySelector(".proc-name")?.textContent.includes(peer.name || peer.pubkey.slice(0,12))) {
        r.classList.add("active");
      }
    });
  }

  const title    = document.getElementById("wg-peer-panel-title");
  const subtitle = document.getElementById("wg-peer-panel-subtitle");
  const nameIn   = document.getElementById("wg-peer-name");
  const delBtn   = document.getElementById("wg-peer-delete-btn");
  const saveBtn  = document.getElementById("wg-peer-save-btn");
  const confSec  = document.getElementById("wg-peer-conf-section");

  showPanelSection("wg-peer-section");

  if (peer) {
    title.textContent    = "Editar dispositivo";
    subtitle.textContent = peer.allowed_ips || peer.pubkey.slice(0, 16) + "…";
    nameIn.value         = peer.name || "";
    delBtn.classList.remove("hidden");
    saveBtn.textContent  = "Guardar nombre";
  } else {
    title.textContent    = "Nuevo dispositivo";
    subtitle.textContent = "Genera un nuevo par de claves";
    nameIn.value         = "";
    delBtn.classList.add("hidden");
    saveBtn.textContent  = "Crear dispositivo";
  }

  if (confSec) confSec.classList.add("hidden");
}

// -------------------------------------------------------
// Load / refresh WireGuard status
// -------------------------------------------------------
function loadWgStatus() {
  wgFetch("/api/wg/status")
    .then(data => {
      _wgState = data;

      renderWgStatus(data);
      renderWgPeersList(data.peers || []);

      // Show correct panel section
      if (!data.initialized) {
        showPanelSection("wg-init-section");
        // Pre-fill endpoint if known
        const epIn = document.getElementById("wg-init-endpoint");
        if (epIn && !epIn.value && data.endpoint) epIn.value = data.endpoint;
      } else if (!_wgSelectedPubkey) {
        showPanelSection("wg-server-section");
        const epIn = document.getElementById("wg-server-endpoint");
        if (epIn) epIn.value = data.endpoint || "";
      }

      // Enable/disable start/stop buttons
      const startBtn = document.getElementById("wg-start-btn");
      const stopBtn  = document.getElementById("wg-stop-btn");
      if (startBtn) startBtn.disabled = data.running;
      if (stopBtn)  stopBtn.disabled  = !data.running;
    })
    .catch(() => {});
}

// -------------------------------------------------------
// Bind all button events (once)
// -------------------------------------------------------
function bindWgEvents() {
  if (window._wgEventsBound) return;
  window._wgEventsBound = true;

  // Start VPN
  document.getElementById("wg-start-btn")?.addEventListener("click", () => {
    wgFetch("/api/wg/start", { method: "POST" }).then(res => {
      if (res.error) alert(res.error);
      else loadWgStatus();
    });
  });

  // Stop VPN
  document.getElementById("wg-stop-btn")?.addEventListener("click", () => {
    if (!confirm("Desactivar WireGuard VPN?")) return;
    wgFetch("/api/wg/stop", { method: "POST" }).then(res => {
      if (res.error) alert(res.error);
      else loadWgStatus();
    });
  });

  // Add peer button
  document.getElementById("wg-add-peer-btn")?.addEventListener("click", () => {
    openPeerEditor(null);
  });

  // Save / create peer
  document.getElementById("wg-peer-save-btn")?.addEventListener("click", () => {
    const name = (document.getElementById("wg-peer-name")?.value || "").trim();

    if (_wgSelectedPubkey) {
      // Rename existing peer
      wgFetch("/api/wg/peer/rename", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pubkey: _wgSelectedPubkey, name })
      }).then(res => {
        if (res.error) { alert(res.error); return; }
        loadWgStatus();
      });
    } else {
      // Create new peer
      if (!name) { alert("Escribe un nombre para el dispositivo."); return; }
      wgFetch("/api/wg/peer/add", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name })
      }).then(res => {
        if (res.error) { alert(res.error); return; }
        // Show generated config
        const confSec  = document.getElementById("wg-peer-conf-section");
        const confPre  = document.getElementById("wg-peer-conf-text");
        const saveBtn  = document.getElementById("wg-peer-save-btn");
        const delBtn   = document.getElementById("wg-peer-delete-btn");
        const subtitle = document.getElementById("wg-peer-panel-subtitle");

        if (confPre) confPre.textContent = res.client_conf || "";
        if (confSec) confSec.classList.remove("hidden");
        _wgSelectedPubkey = res.pubkey;
        if (saveBtn) saveBtn.textContent = "Guardar nombre";
        if (delBtn)  delBtn.classList.remove("hidden");
        if (subtitle) subtitle.textContent = res.ip || "";

        // Show QR code if available
        const qrWrap = document.getElementById("wg-peer-qr");
        if (qrWrap) {
          if (res.qr_svg) {
            qrWrap.innerHTML = res.qr_svg;
            qrWrap.classList.remove("hidden");
          } else {
            qrWrap.classList.add("hidden");
          }
        }

        loadWgStatus();

        // Bind download/copy buttons (after conf appears)
        document.getElementById("wg-peer-conf-download")?.addEventListener("click", () => {
          const text = confPre?.textContent || "";
          const blob = new Blob([text], { type: "text/plain" });
          const a    = document.createElement("a");
          a.href     = URL.createObjectURL(blob);
          a.download = `${(name || "peer").replace(/\s+/g, "_")}.conf`;
          a.click();
        });
        document.getElementById("wg-peer-conf-copy")?.addEventListener("click", () => {
          navigator.clipboard.writeText(confPre?.textContent || "")
            .then(() => alert("Copiado al portapapeles"))
            .catch(() => alert("No se pudo copiar"));
        });
      });
    }
  });

  // Delete peer
  document.getElementById("wg-peer-delete-btn")?.addEventListener("click", () => {
    if (!_wgSelectedPubkey) return;
    if (!confirm("Eliminar este dispositivo?")) return;
    wgFetch("/api/wg/peer/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pubkey: _wgSelectedPubkey })
    }).then(res => {
      if (res.error) { alert(res.error); return; }
      _wgSelectedPubkey = null;
      showPanelSection("wg-server-section");
      loadWgStatus();
    });
  });

  // Cancel peer edit
  document.getElementById("wg-peer-cancel-btn")?.addEventListener("click", () => {
    _wgSelectedPubkey = null;
    showPanelSection("wg-server-section");
    renderWgPeersList(_wgState?.peers || []);
  });

  // Init server
  document.getElementById("wg-init-btn")?.addEventListener("click", () => {
    const endpoint = (document.getElementById("wg-init-endpoint")?.value || "").trim();
    if (!endpoint) { alert("Introduce el endpoint (ej: tu-dominio.ddns.net:51820)"); return; }
    wgFetch("/api/wg/init", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ endpoint })
    }).then(res => {
      if (res.error) { alert(res.error); return; }
      loadWgStatus();
    });
  });

  // Save endpoint (server settings panel)
  document.getElementById("wg-server-save-btn")?.addEventListener("click", () => {
    const endpoint = (document.getElementById("wg-server-endpoint")?.value || "").trim();
    if (!endpoint) { alert("Introduce el endpoint."); return; }
    wgFetch("/api/wg/endpoint", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ endpoint })
    }).then(res => {
      if (res.error) alert(res.error);
      else { loadWgStatus(); }
    });
  });
}

// -------------------------------------------------------
// Tab activation entry point (called from main poller)
// -------------------------------------------------------
function initWireguardTab() {
  bindWgEvents();
  loadWgStatus();
  if (!window._wgPollTimer) {
    window._wgPollTimer = setInterval(loadWgStatus, 5000);
  }
}

function stopWireguardTab() {
  if (window._wgPollTimer) {
    clearInterval(window._wgPollTimer);
    window._wgPollTimer = null;
  }
}

// =====================================================
// Red tab – network info UI
// =====================================================

let _netPrev   = null;
let _netPrevTs = null;

function _netFormatBytes(bytes) {
  if (bytes < 1024 * 1024)       return (bytes / 1024).toFixed(1) + " KB";
  if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + " MB";
  return (bytes / (1024 * 1024 * 1024)).toFixed(2) + " GB";
}

function _netFormatSpeed(bps) {
  if (bps < 1024)        return bps.toFixed(0) + " B/s";
  if (bps < 1024 * 1024) return (bps / 1024).toFixed(1) + " KB/s";
  return (bps / (1024 * 1024)).toFixed(2) + " MB/s";
}

function renderNetIfaces(ifaces) {
  const grid = document.getElementById("net-ifaces-grid");
  if (!grid) return;

  const now = Date.now() / 1000;
  const dt  = _netPrevTs ? (now - _netPrevTs) : null;
  const prevMap = {};
  if (_netPrev) _netPrev.forEach(p => { prevMap[p.name] = p; });

  // Preservar la card de DDNS si existe
  const ddnsCard = document.getElementById("ddns-iface-card");
  grid.innerHTML = "";
  if (ddnsCard) grid.appendChild(ddnsCard);

  ifaces.forEach(iface => {
    const prev = prevMap[iface.name];
    let rxSpeed = null, txSpeed = null;
    if (prev && dt && dt > 0.1) {
      rxSpeed = Math.max(0, (iface.rx_bytes - prev.rx_bytes) / dt);
      txSpeed = Math.max(0, (iface.tx_bytes - prev.tx_bytes) / dt);
    }

    const dot      = iface.up ? `<span class="status ok"></span>` : `<span class="status err"></span>`;
    const speedStr = iface.speed ? `${iface.speed} Mbps` : "";
    const ipStr    = iface.ipv4 || "—";
    const maskStr  = iface.mask || "";
    const macStr   = iface.mac  || "—";
    const ipv6Str  = iface.ipv6 || "";

    const rxStr      = _netFormatBytes(iface.rx_bytes);
    const txStr      = _netFormatBytes(iface.tx_bytes);
    const rxSpeedStr = rxSpeed !== null ? _netFormatSpeed(rxSpeed) : "—";
    const txSpeedStr = txSpeed !== null ? _netFormatSpeed(txSpeed) : "—";

    const ipv6Row = ipv6Str
      ? `<div class="net-detail-row">
           <span class="label">IPv6</span>
           <span class="value net-ipv6">${escHtml(ipv6Str)}</span>
         </div>`
      : "";

    const card = document.createElement("div");
    card.className = "card net-iface-card" + (iface.up ? "" : " net-iface-down");
    card.innerHTML = `
      <div class="net-iface-header">
        <div class="net-iface-name">${dot} ${escHtml(iface.name)}</div>
        <div class="net-iface-speed">${escHtml(speedStr)}</div>
      </div>
      <div class="net-detail-row">
        <span class="label">IPv4</span>
        <span class="value">${escHtml(ipStr)}${maskStr ? " / " + escHtml(maskStr) : ""}</span>
      </div>
      ${ipv6Row}
      <div class="net-detail-row">
        <span class="label">MAC</span>
        <span class="value">${escHtml(macStr)}</span>
      </div>
      <div class="net-traffic-row">
        <div class="net-traffic-col">
          <div class="net-traffic-label">↓ RX</div>
          <div class="net-traffic-total">${escHtml(rxStr)}</div>
          <div class="net-traffic-speed">${escHtml(rxSpeedStr)}</div>
        </div>
        <div class="net-traffic-col">
          <div class="net-traffic-label">↑ TX</div>
          <div class="net-traffic-total">${escHtml(txStr)}</div>
          <div class="net-traffic-speed">${escHtml(txSpeedStr)}</div>
        </div>
      </div>
    `;
    grid.appendChild(card);
  });

  _netPrev   = ifaces;
  _netPrevTs = now;
}

function loadNetworkTab() {
  fetch("/api/network").then(r => r.json()).then(data => {
    const hostnameEl = document.getElementById("net-hostname");
    const gatewayEl  = document.getElementById("net-gateway");
    const dnsEl      = document.getElementById("net-dns");

    if (hostnameEl) hostnameEl.textContent = data.hostname || "—";
    if (gatewayEl)  gatewayEl.textContent  = data.gateway  || "—";
    if (dnsEl)      dnsEl.textContent      = (data.dns || []).join(", ") || "—";

    renderNetIfaces(data.ifaces || []);
  }).catch(() => {});
}


function loadDdnsStatus() {
  if (window.IX2_ROLE === "viewer") return;  // /api/network/ddns exige operator
  fetch("/api/network/ddns").then(r => r.json()).then(d => {
    const grid = document.getElementById("net-ifaces-grid");
    if (!grid || !d.installed) return;
    let card = document.getElementById("ddns-iface-card");
    if (!card) {
      card = document.createElement("div");
      card.id = "ddns-iface-card";
      card.className = "card net-iface-card";
      grid.prepend(card);
    }
    const cls = d.status === "ok" ? "ok" : d.status === "err" ? "err" : "warn";
    card.innerHTML =
      `<div class="net-iface-header">` +
        `<div class="net-iface-name"><span class="status ${cls}"></span> Dynamic DNS</div>` +
        `<div class="net-iface-speed">ddclient</div>` +
      `</div>` +
      `<div class="net-detail-row"><span class="label">Hostname</span><span class="value">${escHtml(d.hostname || "—")}</span></div>` +
      `<div class="net-detail-row"><span class="label">IP pública</span><span class="value">${escHtml(d.public_ip || "—")}</span></div>` +
      `<div class="net-detail-row"><span class="label">Usuario</span><span class="value">${escHtml(d.login || "—")}</span></div>` +
      `<div class="net-detail-row"><span class="label">Estado</span><span class="value">${escHtml(d.status_msg || "—")}</span></div>`;
  }).catch(() => {});
}
function initRedTab() {
  loadNetworkTab();
  loadDdnsStatus();
  loadWolTargets();
  if (!window._netPollStarted) {
    window._netPollStarted = true;
    setInterval(loadNetworkTab, 3000);
    setInterval(loadDdnsStatus, 30000);
  }
}

// Boton "Añadir" de Wake-on-LAN (antes onclick inline en red.html; wolAddTarget esta en core.js).
document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("wol-add-btn")?.addEventListener("click", () => wolAddTarget());
});

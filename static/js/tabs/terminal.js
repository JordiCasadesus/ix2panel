// =====================================================
// Terminal tab – sesión SSH real (xterm.js + /ws/ssh)
// =====================================================

let _termSocket = null;
let _term = null;
let _termFit = null;
let _termConnected = false;
let _xtermLoadPromise = null;

// xterm.js pesa ~290KB (css+js+addon): se carga bajo demanda la primera vez
// que se abre la pestana Terminal, no en cada carga del panel.
function _loadXtermAssets() {
  if (_xtermLoadPromise) return _xtermLoadPromise;
  if (window.Terminal && window.FitAddon) {
    _xtermLoadPromise = Promise.resolve();
    return _xtermLoadPromise;
  }

  const loadCss = (href) => {
    if (document.querySelector(`link[href="${href}"]`)) return;
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = href;
    document.head.appendChild(link);
  };

  const loadScript = (src) => new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = src;
    s.onload = () => resolve();
    s.onerror = () => reject(new Error("No se pudo cargar " + src));
    document.body.appendChild(s);
  });

  loadCss("/static/vendor/xterm/xterm.css");
  _xtermLoadPromise = loadScript("/static/vendor/xterm/xterm.js")
    .then(() => loadScript("/static/vendor/xterm/xterm-addon-fit.js"));
  return _xtermLoadPromise;
}

function _termWsUrl() {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return proto + "//" + window.location.host + "/ws/ssh";
}

function _setTermStatus(text, kind) {
  const el = document.getElementById("terminal-status");
  if (!el) return;
  el.innerHTML = `<span class="status ${escHtml(kind || "warn")}"></span> ${escHtml(text)}`;
}

function _termShowAuthError(text) {
  const msgEl = document.getElementById("terminal-auth-msg");
  if (!msgEl) return;
  if (text) {
    msgEl.textContent = text;
    msgEl.classList.remove("hidden");
  } else {
    msgEl.textContent = "";
    msgEl.classList.add("hidden");
  }
}

function _termOnConnected() {
  if (_termConnected) return;
  _termConnected = true;

  const authBox = document.getElementById("terminal-auth");
  const wrap = document.getElementById("terminal-wrap");
  if (authBox) authBox.classList.add("hidden");
  if (wrap) wrap.classList.remove("hidden");
  _setTermStatus("Conectado", "ok");

  _loadXtermAssets().then(_termBuild).catch((err) => {
    _setTermStatus("Error cargando la terminal (xterm.js)", "err");
    console.error(err);
  });
}

function _termBuild() {
  if (!_term) {
    _term = new Terminal({
      convertEol: true,
      cursorBlink: true,
      fontSize: 14,
      fontFamily: "Consolas, 'Courier New', monospace",
      theme: { background: "#0d1117", foreground: "#e6edf3" },
    });
    _termFit = new FitAddon.FitAddon();
    _term.loadAddon(_termFit);
    _term.open(document.getElementById("terminal-container"));

    _term.onData((data) => {
      if (_termSocket && _termSocket.readyState === WebSocket.OPEN) {
        _termSocket.send(JSON.stringify({ type: "input", data }));
      }
    });

    _term.onResize(({ cols, rows }) => {
      if (_termSocket && _termSocket.readyState === WebSocket.OPEN) {
        _termSocket.send(JSON.stringify({ type: "resize", cols, rows }));
      }
    });

    window.addEventListener("resize", () => {
      if (_termFit && _termConnected) {
        try { _termFit.fit(); } catch (e) {}
      }
    });
  }

  setTimeout(() => {
    if (_termFit) { try { _termFit.fit(); } catch (e) {} }
    if (_term) _term.focus();
  }, 50);
}

function terminalConnect() {
  const userEl = document.getElementById("terminal-user");
  const passEl = document.getElementById("terminal-pass");
  const btn = document.getElementById("terminal-connect");

  _termShowAuthError("");

  const username = (userEl && userEl.value || "").trim();
  const password = (passEl && passEl.value) || "";
  if (!username || !password) {
    _termShowAuthError("Usuario y contraseña son obligatorios");
    return;
  }

  if (btn) btn.disabled = true;
  _setTermStatus("Conectando...", "warn");

  const ws = new WebSocket(_termWsUrl());
  _termSocket = ws;

  ws.addEventListener("message", (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch (e) { return; }

    if (msg.type === "need_auth") {
      ws.send(JSON.stringify({ type: "auth", username, password }));
    } else if (msg.type === "status") {
      _setTermStatus(msg.text || "", _termConnected ? "ok" : "warn");
    } else if (msg.type === "error") {
      _termShowAuthError(msg.error || "Error de conexión");
      _setTermStatus("Error", "err");
      if (btn) btn.disabled = false;
      _termConnected = false;
    } else if (msg.type === "data") {
      if (_term) _term.write(msg.data);
      if (!_termConnected) _termOnConnected();
    }
  });

  ws.addEventListener("close", () => {
    _setTermStatus("Desconectado", "err");
    if (btn) btn.disabled = false;
    _termConnected = false;
  });

  ws.addEventListener("error", () => {
    _termShowAuthError("Error de conexión WebSocket");
    if (btn) btn.disabled = false;
  });
}

function terminalDisconnect() {
  if (_termSocket) {
    try { _termSocket.close(); } catch (e) {}
    _termSocket = null;
  }
  _termConnected = false;

  const authBox = document.getElementById("terminal-auth");
  const wrap = document.getElementById("terminal-wrap");
  if (wrap) wrap.classList.add("hidden");
  if (authBox) authBox.classList.remove("hidden");

  const passEl = document.getElementById("terminal-pass");
  if (passEl) passEl.value = "";

  if (_term) {
    _term.dispose();
    _term = null;
    _termFit = null;
  }
}

function initTerminalTab() {
  // Empieza a precargar xterm.js en segundo plano al abrir la pestana, para
  // que ya este listo cuando el usuario pulse "Conectar" (no bloquea el formulario).
  _loadXtermAssets().catch(() => {});
  if (_termConnected && _termFit) {
    setTimeout(() => { try { _termFit.fit(); } catch (e) {} }, 50);
  }
}

function stopTerminalTab() {
  // La sesión SSH se deja abierta al cambiar de pestaña; se cierra
  // explícitamente con "Cerrar sesión" o si el servidor corta la conexión.
}

document.addEventListener("DOMContentLoaded", () => {
  // Los campos empiezan "readonly" para que el navegador no los rellene solo
  // (ver terminal.html); se activan al primer foco, antes de que el usuario escriba.
  ["terminal-user", "terminal-pass"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("focus", () => el.removeAttribute("readonly"), { once: true });
  });

  const connectBtn = document.getElementById("terminal-connect");
  if (connectBtn) connectBtn.addEventListener("click", terminalConnect);

  const disconnectBtn = document.getElementById("terminal-disconnect");
  if (disconnectBtn) disconnectBtn.addEventListener("click", terminalDisconnect);

  const passEl = document.getElementById("terminal-pass");
  if (passEl) {
    passEl.addEventListener("keydown", (e) => {
      if (e.key === "Enter") terminalConnect();
    });
  }

  const eyeBtn = document.getElementById("terminal-eye-btn");
  if (eyeBtn) {
    eyeBtn.addEventListener("click", () => {
      const inp = document.getElementById("terminal-pass");
      if (!inp) return;
      const show = inp.type === "password";
      inp.type = show ? "text" : "password";
      eyeBtn.innerHTML = show ? "&#128584;" : "&#128065;";
    });
  }
});

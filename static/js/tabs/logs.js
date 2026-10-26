// =====================================================
// Logs tab – backend-driven
// =====================================================

let lastLogs = [];
let logsFollowBottom = true;

function _isNearBottom(el, threshold = 16) {
  if (!el) return true;
  return (el.scrollHeight - el.scrollTop - el.clientHeight) <= threshold;
}

function renderLogs(filter = "all", query = "") {
  const list = document.getElementById("logs-list");
  if (!list) return;

  const shouldStickToBottom = logsFollowBottom || _isNearBottom(list);
  list.innerHTML = '<div class="process-item list-state">Cargando registros…</div>';
  const limit = (filter === "all") ? 1000 : 400;

  fetch(`/api/logs?filter=${encodeURIComponent(filter)}&q=${encodeURIComponent(query)}&limit=${limit}`)
    .then(r => r.json())
    .then(data => {
      if (data.error) {
        list.innerHTML = `<div class="process-item">Error: ${escHtml(data.error)}</div>`;
        return;
      }

      lastLogs = Array.isArray(data.items) ? data.items : [];
      if (!lastLogs.length) {
        list.innerHTML = `<div class="process-item">Sin registros</div>`;
        return;
      }

      lastLogs.forEach(l => {
        const item = document.createElement("div");
        item.className = `process-item row-interactive ${String(l.type).replace(/[^a-z0-9_-]/gi, "")}`;
        item.innerHTML = `
          <span class="process-name">${escHtml(l.title)}</span>
          <span class="process-stats">${escHtml(l.detail)}</span>
        `;
        item.addEventListener("click", () => {
          list
            .querySelectorAll(".process-item.active")
            .forEach(el => el.classList.remove("active"));
          item.classList.add("active");
          updateLogPanel(l);
        });
        list.appendChild(item);
      });

      if (shouldStickToBottom) {
        // Doble tick para asegurar scroll tras reflow/pintado.
        requestAnimationFrame(() => {
          list.scrollTop = list.scrollHeight;
          requestAnimationFrame(() => {
            list.scrollTop = list.scrollHeight;
          });
        });
      }
    })
    .catch(err => {
      list.innerHTML = `<div class="process-item">Error: ${escHtml(err)}</div>`;
    });
}

function updateLogPanel(log) {
  const panel = document.getElementById("logs-panel");
  if (panel) panel.classList.remove("hidden");

  const title = document.getElementById("log-panel-title");
  if (title) title.textContent = log.title;

  const date = document.getElementById("log-date");
  if (date) date.textContent = log.date;

  const source = document.getElementById("log-source");
  if (source) source.textContent = log.source;

  const message = document.getElementById("log-message");
  if (message) message.textContent = log.message;
}

document.addEventListener("DOMContentLoaded", () => {
  if (document.querySelector("form.login-form")) return;
  const list = document.getElementById("logs-list");
  if (list) {
    list.addEventListener("scroll", () => {
      logsFollowBottom = _isNearBottom(list, 24);
    });
  }

  if (window.IX2_ROLE !== "viewer") renderLogs();  // /api/logs exige operator

  document.querySelectorAll("#tab-logs .btn-filter").forEach(btn => {
    btn.addEventListener("click", () => {
      document
        .querySelectorAll("#tab-logs .btn-filter.active")
        .forEach(el => el.classList.remove("active"));
      btn.classList.add("active");
      renderLogs(btn.dataset.filter, document.getElementById("log-search")?.value || "");
    });
  });

  const search = document.getElementById("log-search");
  if (search) {
    search.addEventListener("input", () => {
      const active = document.querySelector("#tab-logs .btn-filter.active");
      const filter = active?.dataset.filter || "all";
      renderLogs(filter, search.value);
    });
  }
});

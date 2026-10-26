/* polish.js – accesibilidad de la barra de pestañas (solo teclado y roles).
   No cambia el comportamiento con ratón. Para volver atrás: borrar este fichero y su <script>. */
(function () {
  "use strict";
  function init() {
    var nav = document.querySelector(".tabs");
    if (!nav) return;
    nav.setAttribute("role", "tablist");
    nav.querySelectorAll("a[data-tab]").forEach(function (a) {
      a.setAttribute("role", "tab");
      a.setAttribute("tabindex", "0");
      a.setAttribute("aria-selected", a.classList.contains("active") ? "true" : "false");
      a.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); a.click(); }
      });
    });
    // Mantener aria-selected sincronizado con la clase .active que pone core.js
    var obs = new MutationObserver(function () {
      nav.querySelectorAll("a[data-tab]").forEach(function (a) {
        a.setAttribute("aria-selected", a.classList.contains("active") ? "true" : "false");
      });
    });
    obs.observe(nav, { attributes: true, subtree: true, attributeFilter: ["class"] });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();

/* Indicador de carga del dashboard: barra animada arriba + aviso "Cargando datos del NAS...".
   core.js llama a window.ix2DashLoaded() cuando llegan los primeros datos de /api/system.
   Si no llegan (error o NAS muy lento), se quita solo a los 60 s. Para quitarlo: borrar este bloque y la llamada en core.js. */
(function () {
  "use strict";
  if (document.querySelector("form.login-form")) return;
  var bar = document.createElement("div");
  bar.className = "ix2-loadbar";
  bar.setAttribute("aria-hidden", "true");
  var badge = document.createElement("div");
  badge.className = "ix2-loadbadge";
  badge.setAttribute("role", "status");
  var spin = document.createElement("span");
  spin.className = "ix2-loadspin";
  var txt = document.createElement("span");
  txt.textContent = "Cargando datos del NAS…";
  badge.appendChild(spin);
  badge.appendChild(txt);
  document.body.appendChild(bar);
  document.body.appendChild(badge);

  var done = false;
  function finish() {
    if (done) return;
    done = true;
    bar.classList.add("ix2-done");
    badge.classList.add("ix2-done");
    setTimeout(function () {
      if (bar.parentNode) bar.parentNode.removeChild(bar);
      if (badge.parentNode) badge.parentNode.removeChild(badge);
    }, 400);
  }
  window.ix2DashLoaded = finish;
  setTimeout(finish, 60000);
})();

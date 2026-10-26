window.PathTreeUI = (() => {
  function normalizePath(path) {
    let p = String(path || "").trim();
    if (!p) return "";
    if (!p.startsWith("/")) p = `/${p}`;
    p = p.replace(/\/{2,}/g, "/");
    if (p.length > 1 && p.endsWith("/")) p = p.slice(0, -1);
    return p;
  }

  function buildTree(paths) {
    const root = { name: "/", path: "/", children: {} };
    (paths || []).forEach(full => {
      const normalized = normalizePath(full);
      if (!normalized || normalized === "/") return;
      const parts = normalized.split("/").filter(Boolean);
      let node = root;
      let acc = "";
      parts.forEach(part => {
        acc += `/${part}`;
        if (!node.children[part]) {
          node.children[part] = { name: part, path: acc, children: {} };
        }
        node = node.children[part];
      });
    });
    return root;
  }

  function renderTree(container, paths, selectedPath, onSelect) {
    if (!container) return;
    container.innerHTML = "";
    const selected = normalizePath(selectedPath || "");
    const tree = buildTree(paths);
    const collapsed = container.__collapsed || {};
    container.__collapsed = collapsed;

    const renderNode = (node, depth) => {
      const names = Object.keys(node.children).sort((a, b) => a.localeCompare(b));
      names.forEach(name => {
        const child = node.children[name];
        const hasChildren = Object.keys(child.children).length > 0;
        const isCollapsed = !!collapsed[child.path];
        const row = document.createElement("div");
        row.className = "tree-item";
        if (child.path === selected) row.classList.add("active");
        row.style.paddingLeft = `${8 + depth * 16}px`;
        row.innerHTML = `<span class="tree-caret">${hasChildren ? (isCollapsed ? "▸" : "▾") : "·"}</span><span class="tree-folder-icon">📁</span><span class="tree-name">${escHtml(child.name)}</span>`;
        row.title = child.path;
        const caret = row.querySelector(".tree-caret");
        if (caret && hasChildren) {
          caret.addEventListener("click", (e) => {
            e.stopPropagation();
            collapsed[child.path] = !isCollapsed;
            renderTree(container, paths, selectedPath, onSelect);
          });
        }
        row.addEventListener("click", () => onSelect && onSelect(child.path, row));
        container.appendChild(row);
        if (!isCollapsed) {
          renderNode(child, depth + 1);
        }
      });
    };

    renderNode(tree, 0);
  }

  function renderFolderList(container, currentPath, names, onSelect) {
    if (!container) return;
    container.innerHTML = "";
    (names || []).forEach(name => {
      const row = document.createElement("div");
      row.className = "tree-item";
      row.style.paddingLeft = "8px";
      const icon = name === ".." ? "⬆️" : "📁";
      row.innerHTML = `<span class="tree-folder-icon">${icon}</span><span class="tree-name">${escHtml(name)}</span>`;
      row.addEventListener("click", () => {
        const base = normalizePath(currentPath || "/");
        const next = name === ".."
          ? base.split("/").slice(0, -1).join("/") || "/"
          : normalizePath(`${base}/${name}`);
        onSelect && onSelect(next, row);
      });
      container.appendChild(row);
    });
  }


  function createLazyBrowser({ container, crumbEl, root, apiUrl, apiParams, onSelect }) {
    const norm = normalizePath;
    let _root = norm(root || "/");
    const _url = apiUrl || "/api/fs/list";
    const _params = apiParams || {};
    const _expanded = new Set([_root]);
    const _children = {};
    let _selected = _root;

    function _updateCrumb(path) {
      if (!crumbEl) return;
      crumbEl.innerHTML = "";
      const rootParts = _root.split("/").filter(Boolean);
      const allParts = norm(path).split("/").filter(Boolean);
      const homeSpan = document.createElement("span");
      homeSpan.className = "crumb";
      homeSpan.textContent = _root === "/" ? "/" : (rootParts.slice(-1)[0] || "/");
      homeSpan.addEventListener("click", () => _select(_root));
      crumbEl.appendChild(homeSpan);
      let acc = _root;
      allParts.slice(rootParts.length).forEach(part => {
        acc = norm(acc + "/" + part);
        const sep = document.createElement("span"); sep.className = "crumb"; sep.textContent = "/";
        crumbEl.appendChild(sep);
        const c = document.createElement("span"); c.className = "crumb"; c.textContent = part;
        const cp = acc; c.addEventListener("click", () => _select(cp));
        crumbEl.appendChild(c);
      });
    }

    async function _load(path) {
      if (_children[path] !== undefined) return;
      _children[path] = null;
      _render();
      try {
        const qs = new URLSearchParams({ path, ..._params });
        const r = await fetch(_url + "?" + qs, { credentials: "same-origin" });
        const data = await r.json();
        _children[path] = (data.dirs || []).filter(n => n !== "..").sort((a,b) => a.localeCompare(b));
      } catch { _children[path] = []; }
      _render();
    }

    function _select(path) {
      _selected = path;
      _updateCrumb(path);
      _render();
      if (onSelect) onSelect(path);
    }

    function _render() {
      if (!container) return;
      container.innerHTML = "";
      _renderNode(_root, 0);
    }

    function _renderNode(path, depth) {
      const name = path === _root
        ? (path.split("/").filter(Boolean).slice(-1)[0] || "/")
        : path.split("/").slice(-1)[0];
      const isExpanded = _expanded.has(path);
      const kids = _children[path];
      const isLoading = kids === null;
      const hasKids = !Array.isArray(kids) || kids.length > 0;
      const caret = isLoading ? "⟳" : (hasKids ? (isExpanded ? "▾" : "▸") : "·");

      const row = document.createElement("div");
      row.className = "tree-item" + (path === _selected ? " active" : "");
      row.style.paddingLeft = (8 + depth * 16) + "px";
      row.innerHTML = `<span class="tree-caret">${caret}</span><span class="tree-folder-icon">📁</span><span class="tree-name">${escHtml(name)}</span>`;
      row.title = path;

      const caretEl = row.querySelector(".tree-caret");
      if (caretEl && !isLoading && hasKids) {
        caretEl.style.cursor = "pointer";
        caretEl.addEventListener("click", e => {
          e.stopPropagation();
          if (isExpanded) _expanded.delete(path); else { _expanded.add(path); _load(path); }
          _render();
        });
      }
      row.addEventListener("click", () => _select(path));
      container.appendChild(row);

      if (isExpanded && Array.isArray(kids)) {
        kids.forEach(child => _renderNode(norm(path + "/" + child), depth + 1));
      }
    }

    _load(_root);
    _updateCrumb(_root);
    return {
      getSelected: () => _selected,
      setRoot: (newRoot) => {
        _root = norm(newRoot); _selected = _root;
        _expanded.clear(); _expanded.add(_root);
        Object.keys(_children).forEach(k => delete _children[k]);
        _load(_root); _updateCrumb(_root);
      },
      setSelected: (path) => {
        _selected = norm(path);
        // Expande y carga la cadena de ancestros para que el nodo quede visible,
        // sin importar a que profundidad este (util al editar algo ya guardado).
        // Cuando _root es "/" el prefijo NO debe duplicar la barra ("/" + "/" =
        // "//"), o startsWith nunca casaria y no se expandiria nada (bug real:
        // se veia al abrir un share existente con el arbol anclado en "/").
        const rootPrefix = _root === "/" ? "/" : _root + "/";
        if (_selected === _root || _selected.startsWith(rootPrefix)) {
          const rootParts = _root.split("/").filter(Boolean);
          const allParts = _selected.split("/").filter(Boolean);
          let acc = _root;
          _expanded.add(acc);
          _load(acc);
          for (let i = rootParts.length; i < allParts.length; i++) {
            acc = norm(acc + "/" + allParts[i]);
            _expanded.add(acc);
            _load(acc);
          }
        }
        _updateCrumb(_selected);
        _render();
      },
      refresh: (path) => {
        const p = norm(path || _selected);
        delete _children[p];
        _expanded.add(p);
        _load(p);
      },
    };
  }
  return {
    normalizePath,
    buildTree,
    renderTree,
    renderFolderList,
    createLazyBrowser,
  };
})();

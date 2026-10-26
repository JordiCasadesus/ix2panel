import os
import errno
import shutil
from pathlib import Path


ALLOWED_ROOTS = [
    Path("/data1"),
    Path("/data2"),
    Path("/mnt"),
    Path("/home"),
    Path("/srv"),
    Path("/media"),
    Path("/run/media"),
]


def _is_allowed(path: Path) -> bool:
    try:
        resolved = path.resolve(strict=False)
    except Exception:
        return False
    return any(str(resolved) == str(r) or str(resolved).startswith(str(r) + "/") for r in ALLOWED_ROOTS)


def _resolve(path_str: str) -> Path:
    return Path(path_str).expanduser().resolve(strict=False)


def list_dir(path_str):
    path = _resolve(path_str)
    if str(path) == "/":
        # Devuelve solo raices permitidas que existan, sin duplicados.
        dirs = []
        seen = set()
        for p in ALLOWED_ROOTS:
            ps = str(p)
            if ps in seen or not p.exists():
                continue
            seen.add(ps)
            dirs.append(ps)
        return {"path": "/", "dirs": dirs}
    if not _is_allowed(path):
        return {"error": "Ruta no permitida"}
    if not path.exists() or not path.is_dir():
        return {"error": "Ruta inválida"}

    items = []
    parent = path.parent
    if parent != path and _is_allowed(parent):
        items.append("..")
    try:
        with os.scandir(path) as it:
            for entry in it:
                if entry.is_dir(follow_symlinks=False):
                    items.append(entry.name)
    except Exception as e:
        return {"error": str(e)}

    return {"path": str(path), "dirs": sorted(items)}


def create_dir(parent_str: str, name: str):
    parent = _resolve(parent_str)
    folder_name = (name or "").strip()
    if not folder_name:
        return {"error": "Nombre de carpeta obligatorio"}
    if folder_name in {".", ".."} or "/" in folder_name or "\\" in folder_name:
        return {"error": "Nombre de carpeta invalido"}
    if not _is_allowed(parent):
        return {"error": "Ruta no permitida"}
    if not parent.exists() or not parent.is_dir():
        return {"error": "Ruta padre invalida"}

    target = parent / folder_name
    target_resolved = _resolve(str(target))
    if not _is_allowed(target_resolved):
        return {"error": "Ruta no permitida"}
    if target_resolved.exists():
        return {"error": "La carpeta ya existe"}

    try:
        target_resolved.mkdir(mode=0o775, parents=False, exist_ok=False)
        return {"ok": True, "path": str(target_resolved)}
    except Exception as e:
        return {"error": str(e)}


def remove_dir(path_str: str):
    target = _resolve(path_str)
    if not _is_allowed(target):
        return {"error": "Ruta no permitida"}
    if not target.exists() or not target.is_dir():
        return {"error": "Carpeta no encontrada"}
    if any(target == r for r in ALLOWED_ROOTS):
        return {"error": "No se puede eliminar una raiz permitida"}

    try:
        shutil.rmtree(target)
        return {"ok": True}
    except Exception as e:
        return {"error": str(e)}

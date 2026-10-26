import os
import re
import subprocess
import shutil
import threading
import pwd
import grp
from typing import Dict, List, Tuple


SMB_CONF = "/etc/samba/smb.conf"
PANEL_SHARES_CONF = "/etc/samba/ix2panel-shares.conf"
INCLUDE_LINE = f"include = {PANEL_SHARES_CONF}"
ALLOWED_ROOTS = ("/data1", "/data2", "/mnt", "/media", "/run/media", "/home", "/srv")
RESERVED_SECTIONS = {"global", "homes", "printers", "print$"}


def _normalize_bool(value: str, default: bool) -> bool:
    if value is None:
        return default
    v = str(value).strip().lower()
    if v in ("yes", "true", "1", "on"):
        return True
    if v in ("no", "false", "0", "off"):
        return False
    return default


def _parse_conf(path: str) -> Dict[str, Dict[str, str]]:
    data: Dict[str, Dict[str, str]] = {}
    if not os.path.exists(path):
        return data
    current = None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                line = (raw or "").strip()
                if not line:
                    continue
                if line.startswith("#") or line.startswith(";"):
                    continue
                if line.startswith("[") and line.endswith("]"):
                    current = line[1:-1].strip()
                    if current:
                        data.setdefault(current, {})
                    continue
                if current and "=" in line:
                    k, v = line.split("=", 1)
                    data[current][k.strip().lower()] = v.strip()
    except Exception:
        return {}
    return data


def _split_valid_users(value: str) -> Tuple[List[str], List[str]]:
    users: List[str] = []
    groups: List[str] = []
    for token in (value or "").split():
        token = token.strip()
        if not token:
            continue
        if token.startswith("@"):
            groups.append(token[1:])
        else:
            users.append(token)
    return users, groups


def _sanitize_share_name(name: str) -> str:
    return (name or "").strip()


def _validate_share_name(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9._ -]{1,64}", name))


def _validate_share_path(path: str) -> bool:
    path = (path or "").strip()
    if not path.startswith("/"):
        return False
    # Sin saltos de linea (inyectarian directivas en smb.conf) ni '..' (saltarian la lista de raices permitidas)
    if any(c in path for c in "\r\n\x00") or ".." in path.split("/"):
        return False
    norm = os.path.normpath(path)
    if not any(norm == root or norm.startswith(root + "/") for root in ALLOWED_ROOTS):
        return False
    # Y tampoco a traves de enlaces simbolicos que salgan de las raices
    real = os.path.realpath(norm)
    return any(real == os.path.realpath(root) or real.startswith(os.path.realpath(root) + "/") for root in ALLOWED_ROOTS)


_PRINCIPAL_RE = re.compile(r"[A-Za-z0-9_.$-]{1,64}")


def _validate_principals(users, groups) -> bool:
    return all(_PRINCIPAL_RE.fullmatch((x or "").strip()) for x in list(users or []) + list(groups or []) if (x or "").strip())


def _remove_section_from_conf(path: str, section: str) -> bool:
    if not os.path.exists(path):
        return False
    target = (section or "").strip().lower()
    if not target:
        return False

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    out: List[str] = []
    in_target = False
    found = False

    for line in lines:
        stripped = (line or "").strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current = stripped[1:-1].strip().lower()
            if current == target:
                in_target = True
                found = True
                continue
            in_target = False
        if not in_target:
            out.append(line)

    if not found:
        return False

    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.writelines(out)
    os.replace(tmp, path)
    return True


def _ensure_include_in_smb_conf() -> None:
    if not os.path.exists(SMB_CONF):
        return
    with open(SMB_CONF, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    if PANEL_SHARES_CONF in text:
        return
    if not text.endswith("\n"):
        text += "\n"
    text += f"\n{INCLUDE_LINE}\n"
    tmp = f"{SMB_CONF}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, SMB_CONF)


def _read_panel_shares() -> Dict[str, Dict[str, str]]:
    sections = _parse_conf(PANEL_SHARES_CONF)
    out: Dict[str, Dict[str, str]] = {}
    for name, cfg in sections.items():
        if name.lower() in RESERVED_SECTIONS:
            continue
        if not cfg.get("path"):
            continue
        out[name] = cfg
    return out


def _write_panel_shares(shares: Dict[str, Dict[str, str]]) -> None:
    lines = ["# Managed by ix2panel. Edit from the web panel.\n\n"]
    for name in sorted(shares.keys(), key=lambda x: x.lower()):
        cfg = shares[name]
        lines.append(f"[{name}]\n")
        lines.append(f"path = {cfg.get('path', '')}\n")
        lines.append(f"browseable = {cfg.get('browseable', 'yes')}\n")
        lines.append(f"read only = {cfg.get('read only', 'no')}\n")
        lines.append(f"guest ok = {cfg.get('guest ok', 'no')}\n")
        valid_users = cfg.get("valid users", "").strip()
        if valid_users:
            lines.append(f"valid users = {valid_users}\n")
        lines.append("\n")
    tmp = f"{PANEL_SHARES_CONF}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.writelines(lines)
    os.replace(tmp, PANEL_SHARES_CONF)


def list_shares():
    system_cfg = _parse_conf(SMB_CONF)
    panel_cfg = _parse_conf(PANEL_SHARES_CONF)
    by_name = {}

    def add_sections(src: str, cfg: Dict[str, Dict[str, str]], editable: bool):
        for name, opts in cfg.items():
            lower = name.lower()
            if lower in RESERVED_SECTIONS:
                continue
            path = (opts.get("path") or "").strip()
            if not path:
                continue
            users, groups = _split_valid_users(opts.get("valid users", ""))
            by_name[name] = {
                "name": name,
                "path": path,
                "read_only": _normalize_bool(opts.get("read only"), default=False),
                "browseable": _normalize_bool(opts.get("browseable"), default=True),
                "guest_ok": _normalize_bool(opts.get("guest ok"), default=False),
                "users": users,
                "groups": groups,
                "source": src,
                "editable": editable,
                "deletable": True,
            }

    add_sections("system", system_cfg, editable=False)
    add_sections("panel", panel_cfg, editable=True)

    items = sorted(by_name.values(), key=lambda x: x["name"].lower())
    return {"items": items}


def _build_valid_users(users: List[str], groups: List[str]) -> str:
    parts = []
    for u in users:
        u = (u or "").strip()
        if u:
            parts.append(u)
    for g in groups:
        g = (g or "").strip()
        if g:
            parts.append("@" + g)
    return " ".join(parts).strip()


def _run_no_fail(cmd: List[str]) -> Tuple[bool, str]:
    try:
        subprocess.check_output(cmd, text=True, errors="replace")
        return True, ""
    except Exception as e:
        return False, str(e)


def _set_fs_permissions_for_share(path: str, users: List[str], groups: List[str]) -> Tuple[bool, str]:
    """
    Ajusta permisos de la carpeta del share para que la gestión desde panel
    también configure acceso Linux básico.
    Reglas:
    - 1 usuario y sin grupos: owner=user, group=grupo primario del user, mode=0770
    - Con grupos: group=primer grupo, mode=2770 (setgid para heredar grupo)
    - Sin datos válidos: mode=0770
    """
    target = os.path.realpath((path or "").strip())
    if not target or not os.path.isdir(target):
        return False, "Ruta invalida para permisos."
    if target in {os.path.realpath(r) for r in ALLOWED_ROOTS}:
        # Nunca chown/chmod recursivo sobre una raiz completa (/data1, /home...): cambiaria el dueño de TODO su contenido.
        return True, ""

    uid = -1
    gid = -1
    mode = 0o770

    clean_users = [(u or "").strip() for u in (users or []) if (u or "").strip()]
    clean_groups = [(g or "").strip() for g in (groups or []) if (g or "").strip()]

    if len(clean_users) == 1:
        try:
            pu = pwd.getpwnam(clean_users[0])
            uid = pu.pw_uid
            gid = pu.pw_gid
        except Exception:
            return False, f"Usuario inexistente para permisos: {clean_users[0]}"

    if clean_groups:
        try:
            pg = grp.getgrnam(clean_groups[0])
            gid = pg.gr_gid
            mode = 0o2770
        except Exception:
            return False, f"Grupo inexistente para permisos: {clean_groups[0]}"

    # chown recursivo + chmod en la raiz.
    if uid != -1 and gid != -1:
        chown_arg = f"{uid}:{gid}"
    elif uid != -1:
        chown_arg = str(uid)
    elif gid != -1:
        chown_arg = f":{gid}"
    else:
        chown_arg = None

    # chmod en la raiz (rapido, sincrono).
    try:
        os.chmod(target, mode)
    except Exception:
        ok, err = _run_no_fail(["sudo", "-n", "chmod", oct(mode)[2:], target])
        if not ok:
            return False, f"No se pudo aplicar permisos chmod: {err}"

    if chown_arg:
        # chown -R puede tardar mucho en directorios grandes: ejecutar en background.
        def _do_chown():
            try:
                subprocess.check_output(["chown", "-R", chown_arg, target], text=True, errors="replace")
            except Exception:
                _run_no_fail(["sudo", "-n", "chown", "-R", chown_arg, target])
        threading.Thread(target=_do_chown, daemon=True).start()

    return True, ""


def _ancestor_dirs(target: str) -> List[str]:
    """Directorios intermedios entre la raiz del sistema y el padre de target
    (sin incluir target ni '/')."""
    norm = os.path.normpath(target)
    parts = [p for p in norm.split("/") if p]
    ancestors = []
    acc = ""
    for part in parts[:-1]:
        acc += "/" + part
        ancestors.append(acc)
    return ancestors


def _grant_parent_traverse(target: str, users: List[str], groups: List[str]) -> List[str]:
    """
    Da permiso de transito (solo ejecucion, via ACL POSIX) en cada directorio
    antecesor de `target` a los usuarios/grupos del share.

    Necesario porque el share puede vivir dentro de otra carpeta con permisos
    mas restrictivos (p.ej. una subcarpeta de un share ya existente, propiedad
    de otro usuario): sin el bit de ejecucion en cada antecesor, Samba no
    puede ni siquiera atravesarlos para llegar al share, aunque el share en
    si tenga los permisos correctos.

    Solo anade una entrada ACL de "--x" (sin lectura): permite pasar por la
    carpeta sin poder listar su contenido. No toca dueño/modo de los antecesores.
    Es best-effort: si `setfacl` no esta disponible, se devuelve un aviso en
    vez de fallar la creacion del share.
    """
    warnings: List[str] = []
    clean_users = [(u or "").strip() for u in (users or []) if (u or "").strip()]
    clean_groups = [(g or "").strip() for g in (groups or []) if (g or "").strip()]
    if not clean_users and not clean_groups:
        return warnings

    for ancestor in _ancestor_dirs(os.path.realpath(target)):
        if not os.path.isdir(ancestor):
            continue
        for u in clean_users:
            ok, err = _run_no_fail(["setfacl", "-m", f"u:{u}:--x", ancestor])
            if not ok:
                ok, err = _run_no_fail(["sudo", "-n", "setfacl", "-m", f"u:{u}:--x", ancestor])
            if not ok:
                warnings.append(f"No se pudo dar transito a '{u}' en {ancestor}: {err}")
        for g in clean_groups:
            ok, err = _run_no_fail(["setfacl", "-m", f"g:{g}:--x", ancestor])
            if not ok:
                ok, err = _run_no_fail(["sudo", "-n", "setfacl", "-m", f"g:{g}:--x", ancestor])
            if not ok:
                warnings.append(f"No se pudo dar transito al grupo '{g}' en {ancestor}: {err}")
    return warnings


def create_share(name: str, path: str, read_only=False, users=None, groups=None):
    name = _sanitize_share_name(name)
    path = (path or "").strip()
    users = users or []
    groups = groups or []

    if not _validate_share_name(name):
        return {"error": "Nombre de share invalido (1-64, letras, numeros, espacio, ._-)."}
    if not _validate_share_path(path):
        return {"error": "Ruta no permitida. Usa /data1, /data2, /mnt, /media, /run/media, /home o /srv."}
    if not _validate_principals(users, groups):
        return {"error": "Usuario o grupo con caracteres no validos."}

    try:
        os.makedirs(path, exist_ok=True)
    except Exception as e:
        return {"error": f"No se pudo crear/verificar ruta: {e}"}

    ok_perm, err_perm = _set_fs_permissions_for_share(path, users, groups)
    if not ok_perm:
        return {"error": f"Share no guardado: no se pudieron aplicar permisos Linux en la ruta ({err_perm})."}
    parent_warnings = _grant_parent_traverse(path, users, groups)

    try:
        _ensure_include_in_smb_conf()
        shares = _read_panel_shares()
        if name in shares:
            return {"error": "Ya existe un share gestionado por el panel con ese nombre."}
        shares[name] = {
            "path": path,
            "browseable": "yes",
            "read only": "yes" if bool(read_only) else "no",
            "guest ok": "no",
            "valid users": _build_valid_users(users, groups),
        }
        _write_panel_shares(shares)
        result = {"ok": True}
        if parent_warnings:
            result["warning"] = "Share creado, pero con avisos de permisos: " + " | ".join(parent_warnings)
        return result
    except Exception as e:
        return {"error": str(e)}


def update_share(name: str, path: str, read_only=False, users=None, groups=None):
    name = _sanitize_share_name(name)
    path = (path or "").strip()
    users = users or []
    groups = groups or []

    if not _validate_share_name(name):
        return {"error": "Nombre de share invalido."}
    if not _validate_share_path(path):
        return {"error": "Ruta no permitida."}
    if not _validate_principals(users, groups):
        return {"error": "Usuario o grupo con caracteres no validos."}
    try:
        os.makedirs(path, exist_ok=True)
    except Exception as e:
        return {"error": f"No se pudo crear/verificar ruta: {e}"}

    ok_perm, err_perm = _set_fs_permissions_for_share(path, users, groups)
    if not ok_perm:
        return {"error": f"Share no actualizado: no se pudieron aplicar permisos Linux en la ruta ({err_perm})."}
    parent_warnings = _grant_parent_traverse(path, users, groups)

    try:
        shares = _read_panel_shares()
        if name not in shares:
            return {"error": "Solo se pueden editar shares creados por el panel."}
        shares[name] = {
            "path": path,
            "browseable": "yes",
            "read only": "yes" if bool(read_only) else "no",
            "guest ok": "no",
            "valid users": _build_valid_users(users, groups),
        }
        _write_panel_shares(shares)
        result = {"ok": True}
        if parent_warnings:
            result["warning"] = "Share actualizado, pero con avisos de permisos: " + " | ".join(parent_warnings)
        return result
    except Exception as e:
        return {"error": str(e)}


def delete_share(name: str):
    name = _sanitize_share_name(name)
    if not _validate_share_name(name):
        return {"error": "Nombre invalido."}
    try:
        shares = _read_panel_shares()
        if name not in shares:
            system_cfg = _parse_conf(SMB_CONF)
            sys_name = None
            for sec in system_cfg.keys():
                if sec.lower() == name.lower() and sec.lower() not in RESERVED_SECTIONS:
                    sys_name = sec
                    break
            if not sys_name:
                # Idempotente: si no existe, lo tratamos como ya eliminado.
                return {"ok": True, "source": "none", "already_missing": True}
            if not _remove_section_from_conf(SMB_CONF, sys_name):
                # Si no se pudo quitar pero ya no está, no bloqueamos.
                check_cfg = _parse_conf(SMB_CONF)
                exists = any(
                    sec.lower() == (sys_name or "").lower() and sec.lower() not in RESERVED_SECTIONS
                    for sec in check_cfg.keys()
                )
                if not exists:
                    return {"ok": True, "source": "system", "already_missing": True}
                return {"error": "No se pudo eliminar el share del smb.conf."}
            return {"ok": True, "source": "system"}
        shares.pop(name, None)
        _write_panel_shares(shares)
        return {"ok": True, "source": "panel"}
    except Exception as e:
        return {"error": str(e)}


def reload_samba():
    attempts = [
        ["sudo", "-n", "systemctl", "reload", "smbd"],
        ["sudo", "-n", "systemctl", "reload", "samba"],
        ["sudo", "-n", "service", "smbd", "reload"],
        ["sudo", "-n", "service", "samba", "reload"],
    ]
    for cmd in attempts:
        try:
            out = subprocess.check_output(cmd, text=True, errors="replace")
            return {"ok": True, "output": out.strip()}
        except Exception:
            continue
    return {"error": "No se pudo recargar Samba (smbd/samba). Revisa sudoers."}


def _is_safe_removable_share_path(path: str) -> bool:
    p = os.path.realpath((path or "").strip())
    if not p.startswith("/"):
        return False
    for root in ALLOWED_ROOTS:
        rr = os.path.realpath(root)
        if p == rr:
            return False
        if p.startswith(rr + "/"):
            return True
    return False


def delete_personal_panel_shares_for_user(username: str, remove_paths: bool = False):
    username = (username or "").strip()
    if not username:
        return {"error": "Usuario invalido."}

    try:
        shares = _read_panel_shares()
        if not shares:
            return {"ok": True, "removed_shares": [], "removed_paths": [], "path_errors": []}

        removed_names: List[str] = []
        removed_paths: List[str] = []
        path_errors: List[str] = []

        keep = {}
        for name, cfg in shares.items():
            users, groups = _split_valid_users(cfg.get("valid users", ""))
            is_personal = (
                len(groups) == 0
                and len(users) == 1
                and (users[0] or "").strip().lower() == username.lower()
            )
            if not is_personal:
                keep[name] = cfg
                continue

            removed_names.append(name)
            if remove_paths:
                path = (cfg.get("path") or "").strip()
                if path and _is_safe_removable_share_path(path):
                    try:
                        if os.path.isdir(path):
                            shutil.rmtree(path)
                            removed_paths.append(path)
                    except Exception as e:
                        path_errors.append(f"{path}: {e}")
                elif path:
                    path_errors.append(f"{path}: ruta no segura para borrar")

        if removed_names:
            _write_panel_shares(keep)

        return {
            "ok": True,
            "removed_shares": removed_names,
            "removed_paths": removed_paths,
            "path_errors": path_errors,
        }
    except Exception as e:
        return {"error": str(e)}

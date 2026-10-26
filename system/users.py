# system/users.py
# =====================================================
# Users - backend Linux-first
# =====================================================

import grp
import os
import pwd
import re
import subprocess

try:
    import spwd
except Exception:
    spwd = None

_USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


def _is_linux():
    return os.name == "posix"


def _require_root():
    try:
        return os.geteuid() == 0
    except Exception:
        return False


def _run(cmd, input_text=None):
    return subprocess.run(
        cmd,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )


def _set_smb_password(name, password):
    if not password:
        return {"ok": True}

    # Update SMB password if user already exists in passdb.
    res = _run(["smbpasswd", name], input_text=f"{password}\n{password}\n")
    if res.returncode != 0:
        # Create SMB passdb entry on first use.
        res = _run(["smbpasswd", "-a", name], input_text=f"{password}\n{password}\n")
        if res.returncode != 0:
            return {"error": res.stderr.strip() or res.stdout.strip() or "Error creando password SMB."}

    _run(["smbpasswd", "-e", name])
    return {"ok": True}


def _validate_username(name):
    return bool(name and _USER_RE.match(name))


_GROUP_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,31}$")


def _validate_password(password):
    # chpasswd lee 'usuario:clave' por lineas: un salto de linea en la clave permitiria cambiar OTRA cuenta.
    return not any(c in (password or "") for c in "\r\n\x00")


def _validate_groups(groups):
    return all(_GROUP_RE.match(str(g or "")) for g in (groups or []))


def _user_groups(username):
    groups = []
    for g in grp.getgrall():
        if username in g.gr_mem:
            groups.append(g.gr_name)
    try:
        primary = pwd.getpwnam(username).pw_gid
        groups.append(grp.getgrgid(primary).gr_name)
    except Exception:
        pass
    return sorted(set(groups))


def get_users():
    users = []
    seen = set()
    for u in pwd.getpwall():
        if u.pw_name in seen:
            continue
        if u.pw_uid < 1000 and u.pw_name != "root":
            continue

        seen.add(u.pw_name)
        groups = _user_groups(u.pw_name)
        status = "activo"
        if spwd:
            try:
                sp = spwd.getspnam(u.pw_name)
                if sp.sp_pwdp and sp.sp_pwdp.startswith(("!", "*")):
                    status = "bloqueado"
            except Exception:
                pass
        users.append({
            "name": u.pw_name,
            "uid": u.pw_uid,
            "role": "admin" if u.pw_uid == 0 or u.pw_name in ("admin", "root") else "user",
            "status": status,
            "group": groups[0] if groups else "users",
            "groups": groups,
            "home": u.pw_dir,
        })

    return users


def create_user(name, password="", admin=False, groups=None):
    if not _is_linux():
        return {"error": "Solo disponible en Linux."}
    if not _require_root():
        return {"error": "Se requieren permisos de root."}
    if not _validate_username(name):
        return {"error": "Nombre de usuario invalido: solo minusculas, numeros, '_' o '-', empezando por letra o '_' (maximo 32 caracteres)."}

    if not _validate_password(password):
        return {"error": "La contrasena contiene caracteres no validos (saltos de linea)."}
    if not _validate_groups(groups):
        return {"error": "Nombre de grupo invalido."}

    extra_groups = set(groups or [])
    if admin:
        extra_groups.add("sudo")

    cmd = ["useradd", "-m", "-s", "/bin/bash"]
    if extra_groups:
        cmd += ["-G", ",".join(sorted(extra_groups))]
    cmd.append(name)
    res = _run(cmd)
    if res.returncode != 0:
        return {"error": res.stderr.strip() or res.stdout.strip() or "Error creando usuario."}

    if password:
        res = _run(["chpasswd"], input_text=f"{name}:{password}")
        if res.returncode != 0:
            return {"error": res.stderr.strip() or res.stdout.strip() or "Error asignando contrasena."}
        smb = _set_smb_password(name, password)
        if smb.get("error"):
            return {"error": smb.get("error")}

    return {"ok": True}


def update_user(name, password=None, admin=None, groups=None, active=None):
    if not _is_linux():
        return {"error": "Solo disponible en Linux."}
    if not _require_root():
        return {"error": "Se requieren permisos de root."}
    if not _validate_username(name):
        return {"error": "Nombre de usuario invalido."}
    if not _validate_password(password):
        return {"error": "La contrasena contiene caracteres no validos (saltos de linea)."}
    if not _validate_groups(groups):
        return {"error": "Nombre de grupo invalido."}

    if groups is not None:
        extra_groups = set(groups)
        if admin:
            extra_groups.add("sudo")
        res = _run(["usermod", "-G", ",".join(sorted(extra_groups)), name])
        if res.returncode != 0:
            return {"error": res.stderr.strip() or res.stdout.strip() or "Error actualizando grupos."}

    if admin is not None and groups is None:
        if admin:
            res = _run(["usermod", "-aG", "sudo", name])
        else:
            res = _run(["gpasswd", "-d", name, "sudo"])
        if res.returncode != 0:
            return {"error": res.stderr.strip() or res.stdout.strip() or "Error actualizando rol."}

    if active is not None:
        res = _run(["usermod", "-U" if active else "-L", name])
        if res.returncode != 0:
            return {"error": res.stderr.strip() or res.stdout.strip() or "Error actualizando estado."}

    if password:
        res = _run(["chpasswd"], input_text=f"{name}:{password}")
        if res.returncode != 0:
            return {"error": res.stderr.strip() or res.stdout.strip() or "Error actualizando contrasena."}
        smb = _set_smb_password(name, password)
        if smb.get("error"):
            return {"error": smb.get("error")}

    return {"ok": True}


def delete_user(name, remove_home=True, remove_personal_shares=False, remove_share_paths=False):
    if not _is_linux():
        return {"error": "Solo disponible en Linux."}
    if not _require_root():
        return {"error": "Se requieren permisos de root."}
    if not _validate_username(name):
        return {"error": "Nombre de usuario invalido."}
    if name in ("root",):
        return {"error": "No se puede eliminar root."}

    cmd = ["userdel"]
    if remove_home:
        cmd.append("-r")
    cmd.append(name)
    res = _run(cmd)
    if res.returncode != 0:
        return {"error": res.stderr.strip() or res.stdout.strip() or "Error eliminando usuario."}

    # Best-effort SMB cleanup
    _run(["pdbedit", "-x", "-u", name])
    _run(["smbpasswd", "-x", name])

    out = {"ok": True}
    if remove_personal_shares:
        try:
            from system.shares import delete_personal_panel_shares_for_user

            sr = delete_personal_panel_shares_for_user(name, remove_paths=bool(remove_share_paths))
            if sr.get("error"):
                out["warning"] = f"Usuario eliminado, pero no se pudieron limpiar shares: {sr.get('error')}"
            else:
                out["removed_shares"] = sr.get("removed_shares", [])
                out["removed_paths"] = sr.get("removed_paths", [])
                out["share_path_errors"] = sr.get("path_errors", [])
        except Exception as e:
            out["warning"] = f"Usuario eliminado, pero no se pudieron limpiar shares: {e}"
    return out

import os
import re
import subprocess
import tempfile
from pathlib import Path


ALLOWED_MOUNT_ROOTS = [
    Path("/mnt"),
    Path("/media"),
]


def _is_allowed_mount(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except Exception:
        return False
    return any(resolved != r and r in resolved.parents for r in ALLOWED_MOUNT_ROOTS)


def list_mounts():
    mounts = []
    try:
        with open("/proc/mounts", "r") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 3:
                    continue
                src, dst, fstype = parts[0], parts[1], parts[2]
                if fstype in ("cifs", "nfs", "nfs4"):
                    mounts.append({"source": src, "target": dst, "type": fstype})
    except Exception as e:
        return {"error": str(e), "items": []}
    return {"items": mounts}


def mount_smb(host, share, username, password, mountpoint, vers="3.0"):
    # Basic validation
    if not re.match(r"^[a-zA-Z0-9._-]+$", host):
        return {"error": "Host inválido"}
    if not re.match(r"^[a-zA-Z0-9._$-]+$", share):
        return {"error": "Share inválido"}

    if not re.fullmatch(r"[0-9.]{1,5}", str(vers)):
        return {"error": "Versión SMB inválida"}
    if any(c in (str(username) + str(password)) for c in "\r\n\x00"):
        return {"error": "Usuario o contraseña con caracteres no válidos"}

    mp = Path(mountpoint)
    if not _is_allowed_mount(mp):
        return {"error": "Ruta de montaje no permitida"}

    # Credenciales en un fichero 0600 en /run (RAM): en la línea de comandos se verían con 'ps'
    # y una coma en la contraseña inyectaría opciones de mount.
    cred_path = None
    try:
        subprocess.check_output(["sudo", "/bin/mkdir", "-p", str(mp)], text=True, errors="replace")
        fd, cred_path = tempfile.mkstemp(prefix=".smbcred_", dir="/run" if os.path.isdir("/run") else None)
        with os.fdopen(fd, "w", encoding="utf-8") as cf:
            cf.write(f"username={username}\npassword={password}\n")
        src = f"//{host}/{share}"
        opts = f"credentials={cred_path},iocharset=utf8,vers={vers}"
        subprocess.check_output(["sudo", "/bin/mount", "-t", "cifs", src, str(mp), "-o", opts],
                                text=True, errors="replace", stderr=subprocess.STDOUT)
        return {"ok": True}
    except Exception as e:
        return {"error": str(e)}
    finally:
        if cred_path:
            try:
                os.remove(cred_path)
            except OSError:
                pass


def unmount(path):
    mp = Path(path)
    if not _is_allowed_mount(mp):
        return {"error": "Ruta de desmontaje no permitida"}
    try:
        subprocess.check_output(["sudo", "/bin/umount", str(mp)], text=True, errors="replace")
        return {"ok": True}
    except Exception as e:
        return {"error": str(e)}

"""Preparar y activar un kernel de bodhi (Kirkwood) desde el panel.

Flujo (solo admin, nunca con un backup en marcha):
  1. save_upload():  recibe el .tar.bz2 y comprueba su sha256 (el que publica bodhi).
  2. start_prepare(): extrae el paquete, instala los modulos en /lib/modules/<version>, copia el config a /boot,
     crea /boot/uImage.new (zImage + DTB del ix2-ng) y /boot/uInitrd.new (initramfs MODULES=dep, gzip, < 11 MB).
     NO toca /boot/uImage ni /boot/uInitrd: el NAS sigue arrancando con el kernel de siempre.
  3. La prueba de arranque se hace a mano desde U-Boot (consola serie) con los .new.
  4. activate(): guarda los actuales como .old y renombra los .new. discard(): borra los .new.

El U-Boot (flash SPI) no se toca nunca. El panel corre con eventlet y un solo worker: lo pesado va por subprocesos
(tar, cp, mkinitramfs, mkimage) para no bloquear el resto del panel.
"""
import hashlib
import json
import os
import platform
import re
import shutil
import struct
import subprocess
import threading
import time
import zlib
from pathlib import Path

BASE = Path("/var/lib/ix2panel/kernel")
BOOT = Path("/boot")
MODULES = Path("/lib/modules")
INITRAMFS_TOOLS = Path("/etc/initramfs-tools")

TOOL_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"
NICE = "/usr/bin/nice"
TAR = "/usr/bin/tar"
CP = "/usr/bin/cp"
DPKG_DEB = "/usr/bin/dpkg-deb"
MKIMAGE = "/usr/bin/mkimage"
MKINITRAMFS = "/usr/sbin/mkinitramfs"
DEPMOD = "/usr/sbin/depmod"

DTB_NAME = "kirkwood-lenovo-ix2-ng.dtb"
MAX_UPLOAD = 120 * 1024 * 1024
# Limite del U-Boot: el uInitrd debe pesar menos de ~11 MB (el actual, de 10,77 MB, arranca). Margen de seguridad.
INITRD_MAX = 10_900_000
MIN_FREE_BASE = 500 * 1024 * 1024
MIN_FREE_BOOT = 40 * 1024 * 1024

NAME_RE = re.compile(r"^linux-(\d{1,3}\.\d{1,3}\.\d{1,3}-kirkwood-tld-\d{1,3})-bodhi\.tar\.bz2$")
VER_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}-kirkwood-tld-\d{1,3}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
BOOT_FILES = ("uImage", "uInitrd", "uImage.old", "uInitrd.old", "uImage.new", "uInitrd.new")

_UIMAGE_MAGIC = 0x27051956
_LOCK = threading.Lock()
_JOB = {"running": False, "step": "", "log": [], "ok": None, "error": None,
        "version": None, "started": None, "finished": None, "result": None}


# ---------------------------------------------------------------- utilidades

def _log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    with _LOCK:
        _JOB["log"].append(line)
        del _JOB["log"][:-300]


def _step(name):
    with _LOCK:
        _JOB["step"] = name
    _log(name)


def _run(cmd, timeout, env=None, nice=True):
    full = ([NICE, "-n", "10"] if nice else []) + [str(c) for c in cmd]
    e = dict(os.environ)
    e["PATH"] = TOOL_PATH
    e["LC_ALL"] = "C"
    if env:
        e.update(env)
    p = subprocess.run(full, capture_output=True, text=True, errors="replace", timeout=timeout, env=e)
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()


def _tail(text, n=6):
    return "\n".join((text or "").splitlines()[-n:])


def _free(path):
    try:
        return shutil.disk_usage(str(path)).free
    except Exception:
        return 0


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
            time.sleep(0)
    return h.hexdigest()


def _read_uimage(path, full=False):
    """Cabecera de 64 bytes de un uImage; con full=True comprueba tambien los CRC y el tamano."""
    try:
        with open(path, "rb") as f:
            h = f.read(64)
            if len(h) < 64:
                return None
            magic, hcrc, ts, size, load, ep, dcrc, os_, arch, typ, comp = struct.unpack(">IIIIIIIBBBB", h[:32])
            if magic != _UIMAGE_MAGIC:
                return None
            info = {"name": h[32:64].split(b"\0", 1)[0].decode("ascii", "replace"), "size": size, "type": typ,
                    "comp": comp, "load": load, "entry": ep, "os": os_, "arch": arch, "time": ts}
            if not full:
                return info
            if zlib.crc32(h[:4] + b"\0\0\0\0" + h[8:64]) & 0xFFFFFFFF != hcrc:
                return None
            crc, left = 0, size
            while left > 0:
                chunk = f.read(min(1 << 20, left))
                if not chunk:
                    return None
                crc = zlib.crc32(chunk, crc)
                left -= len(chunk)
                time.sleep(0)
            if f.read(1) or crc & 0xFFFFFFFF != dcrc:
                return None
            return info
    except OSError:
        return None


def _replace_copy(src, dst):
    """Copia src a dst de forma atomica (fichero temporal + rename)."""
    tmp = Path(str(dst) + ".tmp")
    shutil.copyfile(src, tmp)
    os.chmod(tmp, 0o644)
    os.replace(tmp, dst)


# ---------------------------------------------------------------- estado

def _job_snapshot():
    with _LOCK:
        return {k: (list(v) if isinstance(v, list) else v) for k, v in _JOB.items()}


def _clear_job():
    """Olvida el historial de la ultima preparacion (se llama al activar o descartar)."""
    with _LOCK:
        _JOB.update({"running": False, "step": "", "log": [], "ok": None, "error": None,
                     "version": None, "started": None, "finished": None, "result": None})
    try:
        (BASE / "last_job.json").unlink()
    except OSError:
        pass


def get_job():
    snap = _job_snapshot()
    if not snap["running"] and not snap["log"]:
        try:
            last = json.loads((BASE / "last_job.json").read_text(encoding="utf-8"))
            snap.update({k: last.get(k) for k in ("ok", "error", "version", "started", "finished", "result")})
            snap["log"] = last.get("log", [])
        except Exception:
            pass
    return snap


def get_status():
    files = {}
    for n in BOOT_FILES:
        p = BOOT / n
        try:
            st = p.stat()
        except OSError:
            files[n] = None
            continue
        hdr = _read_uimage(p)
        files[n] = {"size": st.st_size, "mtime": int(st.st_mtime), "name": hdr["name"] if hdr else None}
    try:
        mods = sorted(d.name for d in MODULES.iterdir() if d.is_dir())
    except OSError:
        mods = []
    uploaded = []
    try:
        uploaded = sorted(p.name for p in BASE.glob("linux-*-bodhi.tar.bz2"))
    except OSError:
        pass
    return {
        "running_kernel": platform.release(),
        "files": files,
        "modules": mods,
        "uploaded": uploaded,
        "boot_free": _free(BOOT),
        "new_ready": bool(files.get("uImage.new") and files.get("uInitrd.new")),
        "job": get_job(),
        "initrd_max": INITRD_MAX,
    }


# ---------------------------------------------------------------- subida

def save_upload(stream, filename, sha256, length):
    """Guarda el paquete que se sube. stream: objeto con .read(n). Devuelve {"ok":..} o {"error":..}."""
    m = NAME_RE.match(filename or "")
    if not m:
        return {"error": "Nombre no valido. Debe ser como linux-7.2.11-kirkwood-tld-1-bodhi.tar.bz2"}
    sha = (sha256 or "").strip().lower()
    if not SHA_RE.match(sha):
        return {"error": "El sha256 debe tener 64 caracteres hexadecimales (el que publica bodhi en el hilo)."}
    if not length or length <= 0 or length > MAX_UPLOAD:
        return {"error": "Tamano de subida no valido (maximo 120 MB)."}
    with _LOCK:
        if _JOB["running"]:
            return {"error": "Hay una preparacion en curso."}
    if _free(BASE if BASE.exists() else BASE.parent) < MIN_FREE_BASE:
        return {"error": "Poco espacio libre en el disco del sistema."}
    BASE.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = BASE / "upload.part"
    h = hashlib.sha256()
    total = 0
    try:
        with open(tmp, "wb") as out:
            while True:
                chunk = stream.read(1 << 20)
                if not chunk:
                    break
                total += len(chunk)
                if total > length or total > MAX_UPLOAD:
                    raise ValueError("El fichero es mayor de lo anunciado.")
                h.update(chunk)
                out.write(chunk)
                time.sleep(0)
        if total != length:
            raise ValueError("La subida se ha cortado o esta incompleta.")
        digest = h.hexdigest()
        if digest != sha:
            raise ValueError(f"El sha256 NO coincide. Calculado: {digest}")
        for old in BASE.glob("linux-*-bodhi.tar.bz2"):
            old.unlink()
        os.replace(tmp, BASE / filename)
    except Exception as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        return {"error": str(e)}
    return {"ok": True, "file": filename, "version": m.group(1), "sha256": digest}


# ---------------------------------------------------------------- preparar

def start_prepare():
    ups = sorted(BASE.glob("linux-*-bodhi.tar.bz2")) if BASE.exists() else []
    if not ups:
        return {"error": "No hay ningun paquete subido."}
    m = NAME_RE.match(ups[0].name)
    if not m:
        return {"error": "Paquete con nombre no valido."}
    ver = m.group(1)
    with _LOCK:
        if _JOB["running"]:
            return {"error": "Ya hay una preparacion en curso."}
        _JOB.update({"running": True, "step": "", "log": [], "ok": None, "error": None, "version": ver,
                     "started": int(time.time()), "finished": None, "result": None})
    threading.Thread(target=_prepare_thread, args=(ups[0], ver), daemon=True).start()
    return {"ok": True, "version": ver}


def _prepare_thread(tarball, ver):
    work = BASE / "work"
    err = None
    result = None
    try:
        result = _prepare(tarball, ver, work)
    except Exception as e:
        err = str(e) or e.__class__.__name__
        _log("ERROR: " + err)
    finally:
        shutil.rmtree(work, ignore_errors=True)
        if err is None:
            try:
                tarball.unlink()
            except OSError:
                pass
        with _LOCK:
            _JOB.update({"running": False, "ok": err is None, "error": err, "finished": int(time.time()),
                         "result": result, "step": "Terminado" if err is None else "Fallo"})
        try:
            snap = _job_snapshot()
            (BASE / "last_job.json").write_text(json.dumps(snap), encoding="utf-8")
        except Exception:
            pass


def _need(cond, msg):
    if not cond:
        raise RuntimeError(msg)


def _prepare(tarball, ver, work):
    _need(VER_RE.match(ver), "Version no valida.")
    _step("Comprobando espacio libre")
    _need(_free(BASE) >= MIN_FREE_BASE, "Poco espacio libre en el disco del sistema.")
    _need(_free(BOOT) >= MIN_FREE_BOOT, "Poco espacio libre en /boot.")

    shutil.rmtree(work, ignore_errors=True)
    pkg, debdir, ic, tmpdir = work / "pkg", work / "deb", work / "ic", work / "tmp"
    for d in (pkg, debdir, tmpdir):
        d.mkdir(mode=0o700, parents=True)

    _step("Extrayendo el paquete (puede tardar un minuto)")
    rc, out, err = _run([TAR, "-xjf", tarball, "-C", pkg, "--no-same-owner", "--no-same-permissions"], 600)
    _need(rc == 0, "tar fallo: " + _tail(err or out))
    names = sorted(os.listdir(pkg))
    for n in names:
        p = pkg / n
        _need(p.is_file() and not p.is_symlink(), f"El paquete contiene algo que no es un fichero normal: {n}")
    deb = next((n for n in names if re.match(rf"^linux-image-{re.escape(ver)}_\d+_armel\.deb$", n)), None)
    zimage, config = f"zImage-{ver}", f"config-{ver}"
    dtbtar = f"linux-dtb-{ver}.tar"
    for need_name in (deb, zimage, config, dtbtar):
        _need(need_name and need_name in names, f"Falta en el paquete: {need_name or 'linux-image-...deb'}")
    _log("Contenido: " + ", ".join(names))

    _step("Sacando el DTB del ix2-ng")
    rc, out, err = _run([TAR, "-xf", pkg / dtbtar, "-C", work, "--no-same-owner", "--wildcards", "*" + DTB_NAME], 120)
    _need(rc == 0, "No se pudo extraer el DTB: " + _tail(err or out))
    found = [p for p in work.rglob(DTB_NAME) if p.is_file() and not p.is_symlink()]
    _need(len(found) == 1, f"No se encontro {DTB_NAME} en el paquete.")
    dtb = found[0].read_bytes()
    _need(dtb[:4] == b"\xd0\x0d\xfe\xed" and 1000 < len(dtb) < 1_000_000, "El DTB no parece valido.")

    _step("Extrayendo los modulos del .deb (sin instalarlo)")
    rc, out, err = _run([DPKG_DEB, "-x", pkg / deb, debdir], 600)
    _need(rc == 0, "dpkg-deb fallo: " + _tail(err or out))
    modsrc = debdir / "lib" / "modules" / ver
    _need(modsrc.is_dir(), f"El .deb no trae lib/modules/{ver}.")

    dst = MODULES / ver
    if dst.is_dir() and (dst / "modules.dep").is_file():
        _log(f"/lib/modules/{ver} ya existe: se reutiliza")
    else:
        _step(f"Copiando los modulos a /lib/modules/{ver}")
        tmpmods = MODULES / (ver + ".tmp")
        shutil.rmtree(tmpmods, ignore_errors=True)
        rc, out, err = _run([CP, "-a", modsrc, tmpmods], 600)
        _need(rc == 0, "cp fallo: " + _tail(err or out))
        if dst.exists():
            shutil.rmtree(dst, ignore_errors=True)
        os.rename(tmpmods, dst)
        rc, out, err = _run([DEPMOD, "-a", ver], 300)
        _need(rc == 0, "depmod fallo: " + _tail(err or out))

    _step("Copiando el config a /boot (lo necesita mkinitramfs)")
    _replace_copy(pkg / config, BOOT / config)

    _step("Creando uImage.new (zImage + DTB)")
    fdt = work / "zImage.fdt"
    with open(fdt, "wb") as o, open(pkg / zimage, "rb") as z:
        shutil.copyfileobj(z, o)
        o.write(dtb)
    uimg = work / "uImage.new"
    rc, out, err = _run([MKIMAGE, "-A", "arm", "-O", "linux", "-T", "kernel", "-C", "none", "-a", "0x00008000",
                         "-e", "0x00008000", "-n", f"Linux-{ver}", "-d", fdt, uimg], 300)
    _need(rc == 0, "mkimage (kernel) fallo: " + _tail(err or out))
    _need(_read_uimage(uimg, True) is not None, "El uImage generado no es valido.")

    _step("Generando el initramfs (MODULES=dep, gzip; puede tardar varios minutos)")
    shutil.copytree(INITRAMFS_TOOLS, ic, symlinks=True)
    conf = ic / "initramfs.conf"
    text = conf.read_text(encoding="utf-8", errors="replace") if conf.exists() else ""
    for key, val in (("MODULES", "dep"), ("COMPRESS", "gzip")):
        if re.search(rf"(?m)^{key}=.*$", text):
            text = re.sub(rf"(?m)^{key}=.*$", f"{key}={val}", text)
        else:
            text += f"\n{key}={val}\n"
    conf.write_text(text, encoding="utf-8")
    initrd = work / "initrd.img"
    rc, out, err = _run([MKINITRAMFS, "-d", ic, "-o", initrd, ver], 1200, env={"TMPDIR": str(tmpdir)})
    if rc != 0 or re.search(r"(?m)^E: ", err):
        raise RuntimeError("mkinitramfs fallo: " + _tail(err or out))
    _need(initrd.is_file() and initrd.stat().st_size > 1_000_000, "El initramfs generado esta vacio o es demasiado pequeno.")

    _step("Creando uInitrd.new")
    uinit = work / "uInitrd.new"
    rc, out, err = _run([MKIMAGE, "-A", "arm", "-O", "linux", "-T", "ramdisk", "-C", "gzip", "-a", "0x00000000",
                         "-e", "0x00000000", "-n", ("initramfs-" + ver)[:31], "-d", initrd, uinit], 300)
    _need(rc == 0, "mkimage (initramfs) fallo: " + _tail(err or out))
    size = uinit.stat().st_size
    _log(f"uInitrd.new: {size} bytes (maximo {INITRD_MAX})")
    _need(size <= INITRD_MAX,
          f"El uInitrd pesa {size} bytes y el U-Boot solo admite unos {INITRD_MAX}. NO se instala nada en /boot.")
    _need(_read_uimage(uinit, True) is not None, "El uInitrd generado no es valido.")

    _step("Copiando los .new a /boot")
    _replace_copy(uimg, BOOT / "uImage.new")
    _replace_copy(uinit, BOOT / "uInitrd.new")
    try:
        os.sync()
    except Exception:
        pass
    result = {"version": ver, "uImage.new": _sha256_file(BOOT / "uImage.new"),
              "uInitrd.new": _sha256_file(BOOT / "uInitrd.new"), "uInitrd_bytes": size}
    _log("Listo. Ahora prueba el arranque a mano desde U-Boot con /uImage.new y /uInitrd.new.")
    return result


# ---------------------------------------------------------------- activar / descartar

def activate(expected_version):
    with _LOCK:
        if _JOB["running"]:
            return {"error": "Hay una preparacion en curso."}
    k = _read_uimage(BOOT / "uImage.new", True)
    i = _read_uimage(BOOT / "uInitrd.new", True)
    if not k or not i:
        return {"error": "No hay un par uImage.new / uInitrd.new valido en /boot."}
    ver = k["name"][6:] if k["name"].startswith("Linux-") else ""
    if not VER_RE.match(ver) or ver != (expected_version or ""):
        return {"error": f"La version no coincide (en /boot hay {ver or '?'})."}
    if k["type"] != 2 or k["comp"] != 0 or i["type"] != 3 or i["name"] != ("initramfs-" + ver)[:31]:
        return {"error": "Los .new no son un par kernel/initramfs de la misma version."}
    if i["size"] + 64 > INITRD_MAX:
        return {"error": "El uInitrd.new es demasiado grande para este U-Boot."}
    if not (MODULES / ver / "modules.dep").is_file():
        return {"error": f"Faltan los modulos en /lib/modules/{ver}."}
    if _free(BOOT) < 20 * 1024 * 1024:
        return {"error": "Poco espacio libre en /boot."}

    pairs = (("uImage", "uImage.old", "uImage.new"), ("uInitrd", "uInitrd.old", "uInitrd.new"))
    try:
        for cur, old, _new in pairs:
            if (BOOT / cur).is_file():
                _replace_copy(BOOT / cur, BOOT / old)
        for cur, _old, new in pairs:
            os.replace(BOOT / new, BOOT / cur)
        os.sync()
    except Exception as e:
        # Intento de dejar todo como estaba: si falla a medias, restaurar los actuales desde los .old
        for cur, old, _new in pairs:
            try:
                if (BOOT / old).is_file() and not (BOOT / cur).is_file():
                    _replace_copy(BOOT / old, BOOT / cur)
            except Exception:
                pass
        return {"error": f"No se pudo activar: {e}"}
    _clear_job()
    return {"ok": True, "version": ver,
            "uImage": _sha256_file(BOOT / "uImage"), "uInitrd": _sha256_file(BOOT / "uInitrd")}


def discard():
    with _LOCK:
        if _JOB["running"]:
            return {"error": "Hay una preparacion en curso."}
    removed = []
    for n in ("uImage.new", "uInitrd.new"):
        p = BOOT / n
        try:
            if p.exists():
                p.unlink()
                removed.append(n)
        except OSError as e:
            return {"error": f"No se pudo borrar {n}: {e}"}
    _clear_job()
    for p in (BASE.glob("linux-*-bodhi.tar.bz2") if BASE.exists() else []):
        try:
            p.unlink()
            removed.append(p.name)
        except OSError:
            pass
    return {"ok": True, "removed": removed}

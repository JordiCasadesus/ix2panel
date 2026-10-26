import json
try:
    from system.notifications import send as _notify
except Exception:
    def _notify(*a, **kw): return False
import re
import tempfile
import traceback
import os
import shutil
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path

_RUNNING_JOBS = {}  # job_id -> True mientras está en ejecución
_JOB_OUTPUT = {}   # job_id -> list[str] últimas líneas de rsync
_NOTIFY_STATE = {}  # job_id -> {"status": "ok"|"err", "last_err_sent": epoch}
_NOTIFY_ERR_REPEAT_SEC = 86400   # recordatorio diario mientras el fallo persista
_scheduler_started = False

STATE_PATHS = [
    Path("/var/lib/ix2panel/backup.json"),
    Path.home() / ".ix2panel" / "backup.json",
]

LOG_DIR = Path("/var/lib/ix2panel/backup_logs")

DEFAULT_SCHEDULE = {
    "frequency": "daily",
    "time": "03:00",
}


def _is_ed25519_key(path):
    try:
        with open(path, 'r', errors='ignore') as fh:
            first = fh.read(200)
        return 'OPENSSH PRIVATE KEY' in first
    except Exception:
        return False

def _pick_state_path():
    for p in STATE_PATHS:
        try:
            if p.exists() and os.access(p, os.W_OK):
                return p
            if not p.exists():
                p.parent.mkdir(parents=True, exist_ok=True)
                if os.access(p.parent, os.W_OK):
                    return p
        except Exception:
            continue
    return STATE_PATHS[0]


def _to_bool(value, default=True):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _job_name_from_paths(name, src, dst):
    base = (name or "").strip()
    if base:
        return base
    if dst:
        return f"Backup {Path(dst).name or dst}"
    if src:
        return f"Backup {Path(src).name or src}"
    return "Backup"


def _normalize_schedule(raw):
    data = raw if isinstance(raw, dict) else {}
    return {
        "frequency": str(data.get("frequency") or DEFAULT_SCHEDULE["frequency"]),
        "time": str(data.get("time") or DEFAULT_SCHEDULE["time"]),
    }


def _normalize_smb(raw):
    data = raw if isinstance(raw, dict) else {}
    return {
        "host": str(data.get("host") or ""),
        "share": str(data.get("share") or ""),
        "username": str(data.get("username") or ""),
        "password": str(data.get("password") or ""),
        "mountpoint": str(data.get("mountpoint") or ""),
        "vers": str(data.get("vers") or "3.0"),
        "excludes": str(data.get("excludes") or ""),
    }


def _normalize_ssh(raw):
    data = raw if isinstance(raw, dict) else {}
    def _conv(v):
        s = str(v or "")
        if not s or chr(34) not in s:
            return s
        import re as _re
        items = _re.findall(chr(34) + r"([^" + chr(34) + r"]*)" + chr(34), s)
        return chr(10).join(items) if items else s
    return {
        "host": str(data.get("host") or ""),
        "user": str(data.get("user") or "Administrador"),
        "key_path": str(data.get("key_path") or "/root/.ssh/id_rsa"),
        "port": int(data.get("port") or 22),
        "dst_type": str(data.get("dst_type") or "pc"),
        "src_type": str(data.get("src_type") or "pc"),
        "xd": _conv(data.get("xd") or ""),
        "xf": _conv(data.get("xf") or ""),
        "wg_conf": str(data.get("wg_conf") or ""),
        "nas_user": str(data.get("nas_user") or ""),
        "nas_pass": str(data.get("nas_pass") or ""),
    }


def _normalize_local(raw):
    data = raw if isinstance(raw, dict) else {}
    return {"excludes": str(data.get("excludes") or "")}

def _normalize_job(raw):
    data = raw if isinstance(raw, dict) else {}
    source = str(data.get("source") or "")
    destination = str(data.get("destination") or "")
    mode = str(data.get("mode") or "rsync")
    result = {
        "id": str(data.get("id") or uuid.uuid4().hex[:10]),
        "name": _job_name_from_paths(data.get("name"), source, destination),
        "enabled": _to_bool(data.get("enabled"), True),
        "mode": mode if mode in ("rsync", "ssh", "local") else "rsync",
        "source": source,
        "destination": destination,
        "schedule": _normalize_schedule(data.get("schedule")),
        "status": str(data.get("status") or "warn"),
        "last_backup": str(data.get("last_backup") or ""),
        "free_space": str(data.get("free_space") or ""),
    }
    if isinstance(data.get("smb"), dict):
        result["smb"] = _normalize_smb(data.get("smb"))
    if isinstance(data.get("ssh"), dict):
        result["ssh"] = _normalize_ssh(data.get("ssh"))
    if isinstance(data.get("local"), dict):
        result["local"] = _normalize_local(data.get("local"))
    return result


def _default_store():
    return {
        "version": 2,
        "current_job_id": "",
        "jobs": [],
        "tasks": [],
    }


def _legacy_to_store(raw):
    store = _default_store()
    if not isinstance(raw, dict):
        return store

    jobs = raw.get("jobs")
    if isinstance(jobs, list):
        normalized = [_normalize_job(j) for j in jobs]
        store["jobs"] = normalized
        store["tasks"] = raw.get("tasks") if isinstance(raw.get("tasks"), list) else []
        current = str(raw.get("current_job_id") or "")
        if current and any(j["id"] == current for j in normalized):
            store["current_job_id"] = current
        elif normalized:
            store["current_job_id"] = normalized[0]["id"]
        return store

    # Legacy single job format
    one = _normalize_job({
        "id": raw.get("id") or uuid.uuid4().hex[:10],
        "name": raw.get("name") or "",
        "enabled": raw.get("enabled", True),
        "source": raw.get("source") or "",
        "destination": raw.get("destination") or "",
        "schedule": raw.get("schedule") or {},
        "status": raw.get("status") or "warn",
        "last_backup": raw.get("last_backup") or "",
        "free_space": raw.get("free_space") or "",
    })
    store["jobs"] = [one]
    store["current_job_id"] = one["id"]
    store["tasks"] = raw.get("tasks") if isinstance(raw.get("tasks"), list) else []
    return store


def _job_by_id(store, job_id):
    for job in store.get("jobs", []):
        if job.get("id") == job_id:
            return job
    return None


def _current_job(store):
    jobs = store.get("jobs") or []
    if not jobs:
        return None
    current_id = store.get("current_job_id")
    cur = _job_by_id(store, current_id) if current_id else None
    if cur:
        return cur
    enabled_jobs = [j for j in jobs if j.get("enabled")]
    return enabled_jobs[0] if enabled_jobs else jobs[0]


def _sync_legacy_from_current(store):
    cur = _current_job(store)
    if not cur:
        store["enabled"] = False
        store["status"] = "warn"
        store["last_backup"] = ""
        store["destination"] = ""
        store["source"] = ""
        store["free_space"] = ""
        store["schedule"] = _normalize_schedule({})
    else:
        store["enabled"] = _to_bool(cur.get("enabled"), True)
        store["status"] = str(cur.get("status") or "warn")
        store["last_backup"] = str(cur.get("last_backup") or "")
        store["destination"] = str(cur.get("destination") or "")
        store["source"] = str(cur.get("source") or "")
        store["free_space"] = str(cur.get("free_space") or "")
        store["schedule"] = _normalize_schedule(cur.get("schedule"))
    if not isinstance(store.get("tasks"), list):
        store["tasks"] = []
    return store


def _load_store():
    for p in STATE_PATHS:
        if p.exists():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return _default_store()

            # Detecta si necesita migración: formato legacy o jobs sin id estable.
            raw_jobs = raw.get("jobs") if isinstance(raw, dict) else None
            needs_migration = not (
                isinstance(raw_jobs, list)
                and raw_jobs
                and all(str(j.get("id") or "").strip() for j in raw_jobs if isinstance(j, dict))
            )

            store = _legacy_to_store(raw)
            store = _sync_legacy_from_current(store)

            if needs_migration and store.get("jobs"):
                # Guarda los UUIDs generados para que sean estables en futuras lecturas.
                try:
                    _save_store(store)
                except Exception:
                    pass

            return store
    return _sync_legacy_from_current(_default_store())


def _save_store(store):
    path = _pick_state_path()
    clean_jobs = [_normalize_job(j) for j in (store.get("jobs") or [])]
    store["jobs"] = clean_jobs
    if not store.get("current_job_id") and clean_jobs:
        store["current_job_id"] = clean_jobs[0]["id"]
    if store.get("current_job_id") and not _job_by_id(store, store["current_job_id"]):
        store["current_job_id"] = clean_jobs[0]["id"] if clean_jobs else ""
    store["tasks"] = store.get("tasks") if isinstance(store.get("tasks"), list) else []
    store["version"] = 2
    _sync_legacy_from_current(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass
    return store


def _refresh_free_space(store):
    for job in store.get("jobs", []):
        if job.get("mode") == "ssh":
            job["free_space"] = ""
            continue
        dst = job.get("destination")
        if not dst:
            job["free_space"] = ""
            continue
        try:
            usage = shutil.disk_usage(dst)
            job["free_space"] = f"{round(usage.free / (1024 ** 3), 1)} GB"
        except Exception:
            # Keep prior value if any
            job["free_space"] = str(job.get("free_space") or "")
    _sync_legacy_from_current(store)
    return store


def _is_reachable(host, port=22, timeout=3):
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except Exception:
        return False


def get_backups():
    store = _load_store()
    store = _refresh_free_space(store)
    for job in store.get("jobs", []):
        jid = job.get("id")
        job["running"] = jid in _RUNNING_JOBS
        lines = _JOB_OUTPUT.get(jid, [])
        if lines:
            job["current_line"] = lines[-1]
    return store


def get_running_job_names():
    """Nombres de los backups en ejecución (vacío si no hay ninguno)."""
    ids = list(_RUNNING_JOBS)
    if not ids:
        return []
    try:
        names = {j.get("id"): (j.get("name") or "sin nombre") for j in _load_store().get("jobs", [])}
    except Exception:
        names = {}
    return [names.get(jid, str(jid)) for jid in ids]


# Secretos de un job: (seccion, campo). Un no-admin no los recibe y, si guarda con el campo vacio, se conservan.
_SECRET_FIELDS = (("smb", "password"), ("ssh", "nas_pass"), ("ssh", "wg_conf"))


def _mask_job_secrets(job):
    import copy as _copy
    out = _copy.deepcopy(job)
    for sec, key in _SECRET_FIELDS:
        cfg = out.get(sec)
        if isinstance(cfg, dict):
            cfg[key + "_set"] = bool(cfg.get(key))
            cfg[key] = ""
    return out


def mask_secrets(data):
    """Copia de la respuesta de /api/backups* sin contrasenas (store completo o {"job": ...})."""
    if not isinstance(data, dict):
        return data
    out = dict(data)
    if isinstance(out.get("jobs"), list):
        out["jobs"] = [_mask_job_secrets(j) if isinstance(j, dict) else j for j in out["jobs"]]
    if isinstance(out.get("job"), dict):
        out["job"] = _mask_job_secrets(out["job"])
    return out


def _restore_secrets(section, new_cfg, old_cfg):
    """Si el campo secreto llega vacio, conserva el guardado."""
    if not isinstance(old_cfg, dict):
        return new_cfg
    for sec, key in _SECRET_FIELDS:
        if sec == section and not new_cfg.get(key) and old_cfg.get(key):
            new_cfg[key] = old_cfg[key]
    return new_cfg


def save_backup_config(data, keep_secrets=False):
    # Compatibilidad: si llega formato antiguo, actualiza solo job actual.
    store = _load_store()
    current = _current_job(store)
    if current is None:
        current = _normalize_job({})
        store["jobs"] = [current]
        store["current_job_id"] = current["id"]

    payload = data if isinstance(data, dict) else {}
    if "enabled" in payload:
        current["enabled"] = _to_bool(payload.get("enabled"), True)
    if "mode" in payload:
        current["mode"] = payload["mode"] if payload.get("mode") in ("rsync", "ssh", "local") else "rsync"
    if "source" in payload:
        current["source"] = str(payload.get("source") or "")
    if "destination" in payload:
        current["destination"] = str(payload.get("destination") or "")
    if "name" in payload:
        current["name"] = _job_name_from_paths(payload.get("name"), current.get("source"), current.get("destination"))
    if isinstance(payload.get("schedule"), dict):
        current["schedule"] = _normalize_schedule(payload.get("schedule"))
    if isinstance(payload.get("smb"), dict):
        new_smb = _normalize_smb(payload.get("smb"))
        current["smb"] = _restore_secrets("smb", new_smb, current.get("smb")) if keep_secrets else new_smb
    if isinstance(payload.get("ssh"), dict):
        new_ssh = _normalize_ssh(payload.get("ssh"))
        current["ssh"] = _restore_secrets("ssh", new_ssh, current.get("ssh")) if keep_secrets else new_ssh
    if isinstance(payload.get("local"), dict):
        current["local"] = _normalize_local(payload.get("local"))

    _save_store(store)
    return {"ok": True, "job": current}


def create_backup_job(data):
    payload = data if isinstance(data, dict) else {}
    job = _normalize_job(payload)
    store = _load_store()
    store.setdefault("jobs", []).append(job)
    store["current_job_id"] = job["id"]
    _save_store(store)
    return {"ok": True, "job": job}


def update_backup_job(data, keep_secrets=False):
    payload = data if isinstance(data, dict) else {}
    job_id = str(payload.get("id") or "").strip()
    if not job_id:
        return {"error": "id requerido"}

    store = _load_store()
    job = _job_by_id(store, job_id)
    if not job:
        return {"error": "Backup no encontrado"}

    if "name" in payload:
        job["name"] = _job_name_from_paths(payload.get("name"), job.get("source"), job.get("destination"))
    if "enabled" in payload:
        job["enabled"] = _to_bool(payload.get("enabled"), True)
    if "mode" in payload:
        job["mode"] = payload["mode"] if payload.get("mode") in ("rsync", "ssh", "local") else "rsync"
    if "source" in payload:
        job["source"] = str(payload.get("source") or "")
    if "destination" in payload:
        job["destination"] = str(payload.get("destination") or "")
    if isinstance(payload.get("schedule"), dict):
        job["schedule"] = _normalize_schedule(payload.get("schedule"))
    if isinstance(payload.get("smb"), dict):
        new_smb = _normalize_smb(payload.get("smb"))
        job["smb"] = _restore_secrets("smb", new_smb, job.get("smb")) if keep_secrets else new_smb
    if isinstance(payload.get("ssh"), dict):
        new_ssh = _normalize_ssh(payload.get("ssh"))
        job["ssh"] = _restore_secrets("ssh", new_ssh, job.get("ssh")) if keep_secrets else new_ssh
    if isinstance(payload.get("local"), dict):
        job["local"] = _normalize_local(payload.get("local"))

    _save_store(store)
    return {"ok": True, "job": job}


def delete_backup_job(job_id):
    jid = str(job_id or "").strip()
    if not jid:
        return {"error": "id requerido"}

    store = _load_store()
    jobs = [j for j in (store.get("jobs") or []) if j.get("id") != jid]
    if len(jobs) == len(store.get("jobs") or []):
        return {"error": "Backup no encontrado"}
    store["jobs"] = jobs
    if store.get("current_job_id") == jid:
        store["current_job_id"] = jobs[0]["id"] if jobs else ""
    _save_store(store)
    return {"ok": True}


def select_backup_job(job_id):
    jid = str(job_id or "").strip()
    if not jid:
        return {"error": "id requerido"}
    store = _load_store()
    job = _job_by_id(store, jid)
    if not job:
        return {"error": "Backup no encontrado"}
    store["current_job_id"] = jid
    _save_store(store)
    return {"ok": True, "job": job}


def _mount_smb(smb):
    """Monta un share CIFS. Devuelve (True, '') o (False, error_msg)."""
    host = str(smb.get("host") or "").strip()
    share = str(smb.get("share") or "").strip()
    username = str(smb.get("username") or "").strip()
    password = str(smb.get("password") or "").strip()
    mountpoint = str(smb.get("mountpoint") or "").strip()
    vers = str(smb.get("vers") or "3.0").strip() or "3.0"
    if not host or not share or not mountpoint:
        return False, "SMB: faltan host, share o mountpoint"
    try:
        r = subprocess.run(["mountpoint", "-q", mountpoint], capture_output=True)
        if r.returncode == 0:
            return True, ""  # ya montado
    except Exception:
        pass
    Path(mountpoint).mkdir(parents=True, exist_ok=True)
    if not re.fullmatch(r"[0-9.]{1,5}", vers):
        return False, "SMB: version (vers) no valida"
    if any(c in (username + password) for c in "\r\n\x00"):
        return False, "SMB: usuario o contrasena con caracteres no validos"
    # Credenciales en un fichero 0600 en /run (RAM): en la linea de comandos se verian con 'ps'
    # y una coma en la contrasena rompe/inyecta opciones de mount.
    try:
        cred_fd, cred_path = tempfile.mkstemp(prefix=".smbcred_", dir="/run" if os.path.isdir("/run") else None)
        with os.fdopen(cred_fd, "w", encoding="utf-8") as cf:
            cf.write(f"username={username}\npassword={password}\n")
    except Exception as e:
        return False, f"SMB: no se pudo crear el fichero temporal de credenciales: {e}"
    opts = f"credentials={cred_path},iocharset=utf8,vers={vers}"
    try:
        r = subprocess.run(
            ["mount", "-t", "cifs", f"//{host}/{share}", mountpoint, "-o", opts],
            capture_output=True, text=True, errors="replace", timeout=30,
        )
        if r.returncode == 0:
            return True, ""
        return False, r.stderr.strip() or f"mount error({r.returncode})"
    except subprocess.TimeoutExpired:
        return False, "Timeout al montar SMB"
    except FileNotFoundError:
        return False, "mount.cifs no encontrado — instala cifs-utils"
    except Exception as e:
        return False, str(e)
    finally:
        try:
            os.remove(cred_path)
        except Exception:
            pass


def _umount_smb(mountpoint):
    """Desmonta el mountpoint SMB (ignora errores)."""
    mp = str(mountpoint or "").strip()
    if not mp:
        return
    try:
        subprocess.run(["umount", mp], capture_output=True, timeout=30)
    except Exception:
        pass


def _save_job_log(job_id, job_name, lines, now_str, success, error_msg):
    """Guarda el último log de ejecución en /var/lib/ix2panel/backup_logs/{job_id}.log"""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(LOG_DIR, 0o700)  # los logs pueden contener rutas y usuarios: solo root
        log_path = LOG_DIR / f"{job_id}.log"
        result_line = f"{'OK' if success else 'ERROR'}" + (f": {error_msg}" if error_msg else "")
        content = "\n".join([
            f"=== Backup: {job_name} ===",
            f"=== Fecha: {now_str} ===",
            f"=== Resultado: {result_line} ===",
            "",
        ] + lines + [
            "",
            f"=== FIN {result_line} ===",
        ])
        log_path.write_text(content, encoding="utf-8")
        os.chmod(log_path, 0o600)
    except Exception:
        pass


def get_backup_log(job_id):
    """Lee el último log guardado de un job."""
    jid = str(job_id or "").strip()
    log_path = LOG_DIR / f"{jid}.log"
    if not log_path.exists():
        return {"error": "No hay log guardado para este backup"}
    try:
        return {"log": log_path.read_text(encoding="utf-8")}
    except Exception as e:
        return {"error": str(e)}


def _do_rsync(job_id, source, destination, job_name, smb=None, excludes=None, one_fs=False):
    """Ejecuta rsync en un hilo secundario y actualiza el estado al terminar."""
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    src = source if source.endswith("/") else source + "/"
    success = False
    error_msg = ""
    smb_mounted = False

    # Un origen/destino que empiece por "-" rsync lo leería como opción
    if str(source).lstrip().startswith("-") or str(destination).lstrip().startswith("-"):
        _fail_job(job_id, job_name, "Origen o destino no válido (empieza por '-')")
        return

    # Montar share SMB si está configurado
    if smb and smb.get("host") and smb.get("mountpoint"):
        ok, err = _mount_smb(smb)
        if not ok:
            _fail_job(job_id, job_name, f"No se pudo montar SMB: {err}")
            return
        smb_mounted = True

    _JOB_OUTPUT[job_id] = []
    _timeout_hit = [False]
    try:
        proc = subprocess.Popen(
            (["nice", "-n", "19", "ionice", "-c", "3",
             "rsync", "-avx" if one_fs else "-av", "--delete"]
             + ["--exclude=" + e for e in (excludes or [])]
             + [src, destination]),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace",
        )

        def _kill_on_timeout():
            time.sleep(7200)
            if proc.poll() is None:
                _timeout_hit[0] = True
                proc.kill()

        threading.Thread(target=_kill_on_timeout, daemon=True).start()

        for line in proc.stdout:
            line = line.rstrip("\n\r")
            if line:
                _JOB_OUTPUT[job_id].append(line)
                if len(_JOB_OUTPUT[job_id]) > 500:
                    _JOB_OUTPUT[job_id] = _JOB_OUTPUT[job_id][-400:]

        proc.wait()
        if _timeout_hit[0]:
            error_msg = "Timeout: el backup tardó más de 2 horas"
        else:
            success = proc.returncode == 0
            if not success:
                error_msg = f"rsync salió con código {proc.returncode}"
    except FileNotFoundError:
        error_msg = "rsync no encontrado — instálalo con: apt install rsync"
    except Exception as e:
        error_msg = str(e)
    finally:
        _RUNNING_JOBS.pop(job_id, None)
        if smb_mounted:
            _umount_smb(smb.get("mountpoint"))
        def _cleanup_output():
            time.sleep(300)
            _JOB_OUTPUT.pop(job_id, None)
        threading.Thread(target=_cleanup_output, daemon=True).start()

    _save_job_log(job_id, job_name, list(_JOB_OUTPUT.get(job_id, [])), now_str, success, error_msg)

    store = _load_store()
    job = _job_by_id(store, job_id)
    if job:
        job["last_backup"] = now_str
        job["status"] = "ok" if success else "err"
    detail = f"{'OK' if success else 'ERROR'} - {now_str}"
    if error_msg:
        detail += f": {error_msg}"
    store.setdefault("tasks", [])
    store["tasks"].insert(0, {
        "name": f"Backup: {job_name}",
        "detail": detail,
        "job_id": job_id,
    })
    store["tasks"] = store["tasks"][:6]
    _save_store(store)
    _notify_backup(job_id, job_name, success, error_msg)



def _notify_backup(job_id, job_name, success, error_msg=None):
    """Avisa por Telegram del resultado de un backup.

    Los errores se notifican al entrar en fallo y luego como mucho una vez al
    dia: el scheduler reintenta cada hora, y sin esto un PC apagado generaria
    ~21 mensajes diarios. Los OK se notifican siempre (uno por ejecucion).
    """
    if success:
        _NOTIFY_STATE[job_id] = {"status": "ok", "last_err_sent": 0}
        _notify("OK Backup: " + job_name, event="backup_ok")
        return

    st = _NOTIFY_STATE.get(job_id) or {}
    last_sent = st.get("last_err_sent", 0)
    if st.get("status") == "err" and (time.time() - last_sent) < _NOTIFY_ERR_REPEAT_SEC:
        _NOTIFY_STATE[job_id] = {"status": "err", "last_err_sent": last_sent}
        return   # mismo fallo ya avisado hace menos de 24h

    _NOTIFY_STATE[job_id] = {"status": "err", "last_err_sent": time.time()}
    msg = "ERROR Backup: " + job_name
    if error_msg:
        msg += "\n" + error_msg[:200]
    _notify(msg, event="backup_err")


def _fail_job(job_id, job_name, error_msg, lines=None):
    """Marca un job como fallido, lo registra (tasks + log) y avisa por Telegram.
    Para los fallos que abortan antes de completar rsync/robocopy."""
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    _RUNNING_JOBS.pop(job_id, None)
    store = _load_store()
    job = _job_by_id(store, job_id)
    if job:
        job["status"] = "err"
        job["last_backup"] = now_str
    store.setdefault("tasks", []).insert(0, {
        "name": f"Backup: {job_name}",
        "detail": f"ERROR - {now_str}: {error_msg}",
        "job_id": job_id,
    })
    store["tasks"] = store["tasks"][:6]
    _save_store(store)
    _save_job_log(job_id, job_name, list(lines or []), now_str, False, error_msg)
    _notify_backup(job_id, job_name, False, error_msg)

def _get_df_free(path):
    try:
        import shutil
        return shutil.disk_usage(path).free
    except Exception:
        return 0

def get_backup_progress(job_id):
    jid = str(job_id or "").strip()
    meta = _RUNNING_JOBS.get(jid)
    running = meta is not None
    result = {
        "lines": list(_JOB_OUTPUT.get(jid, [])),
        "running": running,
        "bytes_copied": None,
        "bytes_total": None,
        "percent": None,
        "speed_bps": None,
        "eta_sec": None,
        "elapsed_sec": None,
    }
    if not running or not isinstance(meta, dict):
        return result
    start_time = meta.get("start_time", 0)
    df_free_start = meta.get("df_free_start", 0)
    dest_path = meta.get("dest_path", "")
    bytes_total = meta.get("bytes_total")
    elapsed = time.time() - start_time if start_time else 0
    result["elapsed_sec"] = int(elapsed)
    if dest_path and df_free_start:
        df_free_now = _get_df_free(dest_path)
        bytes_copied = max(0, df_free_start - df_free_now)
        result["bytes_copied"] = bytes_copied
        if bytes_total and bytes_total > 0:
            result["bytes_total"] = bytes_total
            result["percent"] = min(99, int(bytes_copied * 100 / bytes_total))
            if elapsed > 10:
                speed = bytes_copied / elapsed
                result["speed_bps"] = int(speed)
                remaining = bytes_total - bytes_copied
                result["eta_sec"] = int(remaining / speed) if speed > 0 else None
    return result


_PS_BAD_PATH = set('"`\x00')
_PS_BAD_CRED = set('"\'`;&|<>\x00 \t\r\n')


_KNOWN_HOSTS = Path("/var/lib/ix2panel/known_hosts")
_KNOWN_HOSTS_LOCK = threading.Lock()


def _ssh_client(paramiko_mod):
    """Cliente SSH que RECUERDA la huella de cada PC la primera vez que se conecta y rechaza la
    conexion si despues cambia (alguien suplantando al PC). Antes aceptaba cualquier huella.
    Si reinstalas el PC (cambia su huella): sudo ssh-keygen -R <ip> -f /var/lib/ix2panel/known_hosts"""
    client = paramiko_mod.SSHClient()
    try:
        _KNOWN_HOSTS.parent.mkdir(parents=True, exist_ok=True)
        if not _KNOWN_HOSTS.exists():
            _KNOWN_HOSTS.touch(mode=0o600)
        client.load_host_keys(str(_KNOWN_HOSTS))
    except Exception:
        pass

    class _RememberFirst(paramiko_mod.MissingHostKeyPolicy):
        def missing_host_key(self, cl, hostname, key):
            with _KNOWN_HOSTS_LOCK:
                try:
                    cl.load_host_keys(str(_KNOWN_HOSTS))  # por si otro hilo ya guardo algo
                except Exception:
                    pass
                known = cl.get_host_keys().lookup(hostname)
                if known:
                    if known.get(key.get_name()) == key:
                        return  # la misma huella que acaba de guardar otro hilo (el panel abre 2 conexiones a la vez)
                    # Ya conocemos este PC pero con otra clave: no se acepta una segunda en silencio.
                    raise paramiko_mod.SSHException(
                        f"La huella SSH de {hostname} no coincide con la guardada. Si has reinstalado el PC: "
                        f"sudo ssh-keygen -R {hostname} -f {_KNOWN_HOSTS}")
                cl.get_host_keys().add(hostname, key.get_name(), key)
                try:
                    cl.save_host_keys(str(_KNOWN_HOSTS))
                    os.chmod(_KNOWN_HOSTS, 0o600)
                except Exception:
                    pass

    client.set_missing_host_key_policy(_RememberFirst())
    return client


def _check_robocopy_inputs(pc_src, pc_dst, xd, xf, nas_user, nas_pass):
    """Devuelve un mensaje de error si algun valor podria romper o inyectar la orden que se
    ejecuta en el PC Windows; '' si todo es seguro."""
    for label, val, multiline in (("origen", pc_src, False), ("destino", pc_dst, False),
                                  ("exclusion de carpetas", xd, True), ("exclusion de ficheros", xf, True)):
        v = str(val or "")
        if any(c in _PS_BAD_PATH for c in v) or (not multiline and any(c in v for c in "\r\n")):
            return f"Caracteres no permitidos en {label} del backup (comillas dobles, acento grave, saltos de linea)."
    for label, val in (("usuario NAS", nas_user), ("contrasena NAS", nas_pass)):
        v = str(val or "")
        if any(c in _PS_BAD_CRED for c in v) or "$(" in v:
            return (f"El {label} contiene caracteres que la orden de Windows no admite "
                    "(espacios, comillas, acento grave, ; & | < > o '$('). Usa otra sin esos caracteres.")
    return ""


def _do_ssh_robocopy(job_id, ssh_cfg, pc_src, pc_dst, job_name):
    """Ejecuta robocopy en el PC Windows via SSH (paramiko) y transmite la salida."""
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    success = False
    error_msg = ""

    try:
        import paramiko
    except ImportError:
        _fail_job(job_id, job_name, "paramiko no instalado — pip install paramiko")
        return

    host = ssh_cfg.get("host", "")
    user = ssh_cfg.get("user", "Administrator")
    key_path = ssh_cfg.get("key_path", "/root/.ssh/id_rsa")
    port = int(ssh_cfg.get("port") or 22)
    # Auto-detectar IP del NAS: abrimos UDP hacia el PC y leemos la IP local usada
    try:
        _s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        _s.connect((host, port))
        nas_ip = _s.getsockname()[0]
        _s.close()
    except Exception:
        nas_ip = ""

    def _to_unc(linux_path, label):
        if not nas_ip:
            raise ValueError(f"No se pudo detectar la IP del NAS para construir ruta UNC de {label}")
        parts = linux_path.lstrip("/").split("/")
        share = parts[0]
        subpath = "\\".join(parts[1:])
        unc = f"\\\\{nas_ip}\\{share}"
        return unc + f"\\{subpath}" if subpath else unc

    try:
        if pc_src.startswith("/"):
            pc_src = _to_unc(pc_src, "origen")
        if pc_dst.startswith("/"):
            pc_dst = _to_unc(pc_dst, "destino")
    except Exception as e:
        _fail_job(job_id, job_name, str(e))
        return

    xd = ssh_cfg.get("xd", "").strip()
    xf = ssh_cfg.get("xf", "").strip()
    nas_user = ssh_cfg.get("nas_user", "").strip()
    nas_pass = ssh_cfg.get("nas_pass", "").strip()
    _err = _check_robocopy_inputs(pc_src, pc_dst, xd, xf, nas_user, nas_pass)
    if _err:
        _fail_job(job_id, job_name, _err)
        return

    # Construir script PowerShell con net use si hay rutas UNC (NAS)
    unc_paths = [p for p in [pc_src, pc_dst] if p.startswith("\\\\")]
    net_use_cmds = []
    net_del_cmds = []
    if nas_user:
        for unc in unc_paths:
            parts = unc.lstrip("\\").split("\\")
            share_path = f"\\\\{parts[0]}\\{parts[1]}" if len(parts) >= 2 else unc
            if not re.fullmatch(r"\\\\[0-9A-Za-z.:-]+\\[A-Za-z0-9._$-]+", share_path):
                _fail_job(job_id, job_name, f"Nombre de carpeta compartida no valido para 'net use': {share_path}")
                return
            net_use_cmds.append(f"net use {share_path} {nas_pass} /user:{nas_user} /persistent:no")
            net_del_cmds.append(f"net use {share_path} /delete /y 2>&1 | Out-Null")

    # Rutas con comillas simples para PowerShell (evita conflicto con -Command "...")
    # Parsear exclusiones: una por linea, sin comillas -> quoted para robocopy
    def _excl_to_ps(val):
        items = [l.strip() for l in val.splitlines() if l.strip()]
        return " ".join("'" + i.replace("'", "''") + "'" for i in items)
    xd_ps = _excl_to_ps(xd)
    xf_ps = _excl_to_ps(xf)
    _q = lambda s: s.replace("'", "''")  # comilla simple de PowerShell = dos comillas simples
    robocopy_cmd = f"robocopy '{_q(pc_src)}' '{_q(pc_dst)}' /MIR /MT:8 /R:1 /W:1 /NP /A-:SH"
    if xd_ps:
        robocopy_cmd += f" /XD {xd_ps}"
    if xf_ps:
        robocopy_cmd += f" /XF {xf_ps}"

    if net_use_cmds:
        # PowerShell: montar share, ejecutar robocopy, desmontar, salir con exit code de robocopy
        ps_parts = net_use_cmds + [robocopy_cmd, "$rc=$LASTEXITCODE"] + net_del_cmds + ["exit $rc"]
        ps_script = "; ".join(ps_parts)
        cmd = f'powershell -NonInteractive -Command "{ps_script}"'
    else:
        # Sin net use: robocopy directo con comillas dobles normales
        cmd = f'cmd /c robocopy "{pc_src}" "{pc_dst}" /MIR /MT:8 /R:1 /W:1 /NP'
        if xd:
            cmd += " /XD " + " ".join('"' + i.strip() + '"' for i in xd.splitlines() if i.strip())
        if xf:
            cmd += " /XF " + " ".join('"' + i.strip() + '"' for i in xf.splitlines() if i.strip())

    _cmd_log = cmd.replace(nas_pass, "********") if nas_pass else cmd  # la contrasena no debe quedar en el log
    _JOB_OUTPUT[job_id] = [f"[CMD] {_cmd_log}", "[...] Ejecutando robocopy, streaming activo..."]
    _timeout_hit = [False]
    try:
        client = _ssh_client(paramiko)
        try:
            pkey = paramiko.Ed25519Key.from_private_key_file(key_path)
        except Exception:
            pkey = None
        if pkey:
            client.connect(host, port=port, username=user, pkey=pkey, timeout=15, allow_agent=False, look_for_keys=False)
        else:
            client.connect(host, port=port, username=user, key_filename=key_path, timeout=15)
        _, stdout, _ = client.exec_command(cmd)
        channel = stdout.channel
        channel.set_combine_stderr(True)
        def _kill_on_timeout():
            time.sleep(86400)  # 24 horas max
            if not channel.closed:
                _timeout_hit[0] = True
                channel.close()
        threading.Thread(target=_kill_on_timeout, daemon=True).start()
        buf = b""
        NL = bytes([10])
        while True:
            chunk = channel.recv(4096)
            if not chunk:
                break
            buf += chunk
            while NL in buf:
                line_b, buf = buf.split(NL, 1)
                line_s = line_b.decode("utf-8", errors="replace").rstrip(chr(13))
                if line_s.strip() and job_id in _JOB_OUTPUT:
                    _JOB_OUTPUT[job_id].append(line_s)
                    if len(_JOB_OUTPUT[job_id]) > 500:
                        _JOB_OUTPUT[job_id] = _JOB_OUTPUT[job_id][-400:]
        exit_code = channel.recv_exit_status()
        try:
            client.close()
        except Exception:
            pass
        if _timeout_hit[0]:
            error_msg = "Timeout: el backup tardó más de 24 horas"
        else:
            success = exit_code < 8
            if not success:
                error_msg = f"robocopy salió con código {exit_code}"
    except Exception as e:
        error_msg = traceback.format_exc()
    finally:
        _RUNNING_JOBS.pop(job_id, None)

        def _cleanup():
            time.sleep(300)
            _JOB_OUTPUT.pop(job_id, None)
        threading.Thread(target=_cleanup, daemon=True).start()

    _save_job_log(job_id, job_name, list(_JOB_OUTPUT.get(job_id, [])), now_str, success, error_msg)

    store = _load_store()
    job = _job_by_id(store, job_id)
    if job:
        job["last_backup"] = now_str
        job["status"] = "ok" if success else "err"
    detail = f"{'OK' if success else 'ERROR'} - {now_str}"
    if error_msg:
        detail += f": {error_msg}"
    store.setdefault("tasks", []).insert(0, {
        "name": f"Backup: {job_name}",
        "detail": detail,
        "job_id": job_id,
    })
    store["tasks"] = store["tasks"][:6]
    _save_store(store)
    _notify_backup(job_id, job_name, success, error_msg)


_SSH_KEY_DIRS = ("/root/.ssh", "/etc/ix2panel/keys")


def _safe_key_path(key_path, default="/root/.ssh/id_rsa"):
    """Valida la ruta de clave que llega por la API: solo dentro de _SSH_KEY_DIRS,
    nombre simple y sin saltos con '..' ni symlinks. Devuelve la ruta sin '.pub'."""
    kp = os.path.realpath(str(key_path or default).strip())
    if kp.endswith(".pub"):
        kp = kp[:-4]
    if not any(kp.startswith(d + "/") for d in _SSH_KEY_DIRS):
        raise ValueError("Ruta de clave no permitida")
    import re as _re
    if not _re.fullmatch(r"[A-Za-z0-9._-]+", os.path.basename(kp)):
        raise ValueError("Nombre de clave no valido")
    return kp


def get_ssh_pubkey(key_path):
    """Lee y devuelve la clave pública SSH (.pub) para la clave privada indicada."""
    try:
        kp = _safe_key_path(key_path)
    except ValueError as e:
        return {"error": str(e)}
    pub_path = kp + ".pub"
    try:
        content = Path(pub_path).read_text(encoding="utf-8").strip()
        return {"pubkey": content, "path": pub_path}
    except FileNotFoundError:
        return {"error": f"No existe: {pub_path} — ejecuta: ssh-keygen -t ed25519 -f {kp} -N \"\""}
    except Exception as e:
        return {"error": str(e)}



def generate_ssh_key(key_path):
    try:
        kp = _safe_key_path(key_path)
    except ValueError as e:
        return {'error': str(e)}
    pub_path = kp + '.pub'
    if Path(pub_path).exists():
        content = Path(pub_path).read_text(encoding='utf-8').strip()
        return {'pubkey': content, 'path': pub_path, 'already_existed': True}
    try:
        Path(kp).parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(
            ['ssh-keygen', '-t', 'ed25519', '-f', kp, '-N', ''],
            capture_output=True, text=True
        )
        if r.returncode != 0:
            return {'error': r.stderr.strip() or 'ssh-keygen failed'}
        content = Path(pub_path).read_text(encoding='utf-8').strip()
        return {'pubkey': content, 'path': pub_path, 'already_existed': False}
    except Exception as e:
        return {'error': str(e)}

def browse_pc_dirs(job_id, path):
    """Lista directorios en el PC Windows via SSH. Devuelve {"dirs": [...], "path": "..."}."""
    store = _load_store()
    job = _job_by_id(store, job_id)
    if not job:
        return {"error": "Job no encontrado"}
    ssh_cfg = job.get("ssh") if isinstance(job.get("ssh"), dict) else {}
    if not ssh_cfg.get("host"):
        return {"error": "SSH no configurado en este job"}

    try:
        import paramiko
    except ImportError:
        return {"error": "paramiko no instalado"}

    host = ssh_cfg.get("host", "")
    user = ssh_cfg.get("user", "Administrator")
    key_path = ssh_cfg.get("key_path", "/root/.ssh/id_rsa")
    port = int(ssh_cfg.get("port") or 22)
    win_path = (path or "").strip()
    if any(c in win_path for c in '"`\r\n\x00'):
        return {"error": "Ruta no valida"}
    ps_path = win_path.replace("'", "''")
    is_root = win_path in ("", "\\", "/")

    try:
        client = _ssh_client(paramiko)
        try:
            pkey = paramiko.Ed25519Key.from_private_key_file(key_path)
        except Exception:
            pkey = None
        if pkey:
            client.connect(host, port=port, username=user, pkey=pkey, timeout=10, allow_agent=False, look_for_keys=False)
        else:
            client.connect(host, port=port, username=user, key_filename=key_path, timeout=10)
        if is_root:
            cmd = 'powershell -Command "Get-PSDrive -PSProvider FileSystem | ForEach-Object { $_.Root }"'
        else:
            cmd = (
                f"powershell -Command \""
                f"Get-ChildItem -Path '{ps_path}' -Directory -ErrorAction SilentlyContinue"
                f" | Select-Object -ExpandProperty Name\""
            )
        _, stdout, _ = client.exec_command(cmd, timeout=15)
        output = stdout.read().decode("utf-8", errors="replace")
        client.close()
        dirs = [d.strip() for d in output.splitlines() if d.strip()]
        return {"dirs": dirs, "path": win_path, "is_root": is_root}
    except Exception as e:
        return {"error": str(e)}


def run_backup(job_id=None):
    store = _load_store()
    jid = str(job_id or "").strip()
    job = _job_by_id(store, jid) if jid else _current_job(store)
    if not job:
        return {"error": "No hay backups configurados"}
    if not _to_bool(job.get("enabled"), True):
        return {"error": "Backup desactivado. Actívalo para ejecutar."}

    jid = job["id"]
    if jid in _RUNNING_JOBS:
        return {"error": "El backup ya está en ejecución"}

    source = (job.get("source") or "").strip()
    destination = (job.get("destination") or "").strip()
    mode = job.get("mode", "rsync")

    if not source:
        return {"error": "Origen no configurado en el editor"}
    if not destination:
        return {"error": "Destino no configurado en el editor"}

    store["current_job_id"] = jid
    _save_store(store)

    if mode == "ssh":
        ssh_cfg = job.get("ssh") if isinstance(job.get("ssh"), dict) else {}
        if not ssh_cfg.get("host"):
            return {"error": "SSH: host (IP del PC) no configurado"}
        ssh_port = int(ssh_cfg.get("port") or 22)
        if not _is_reachable(ssh_cfg["host"], ssh_port):
            return {"error": f"PC no accesible ({ssh_cfg['host']}:{ssh_port})"}
        if destination.startswith("/") and not Path(destination).exists():
            try:
                Path(destination).mkdir(parents=True, exist_ok=True)
                nas_user = ssh_cfg.get("nas_user", "").strip()
                if nas_user:
                    import shutil, pwd as _pwd
                    try:
                        pw = _pwd.getpwnam(nas_user)
                        os.chown(destination, pw.pw_uid, pw.pw_gid)
                    except Exception:
                        pass
            except Exception as e:
                return {"error": f"Destino no existe y no se pudo crear: {destination} ({e})"}
        _RUNNING_JOBS[jid] = {
            "start_time": time.time(),
            "df_free_start": _get_df_free(destination) if destination.startswith("/") else 0,
            "dest_path": destination,
            "bytes_total": None,
        }
        def _fetch_total_ssh(jid=jid, ssh_cfg=ssh_cfg, source=source):
            try:
                import paramiko as _pm
                cl = _ssh_client(_pm)
                kp = ssh_cfg.get("key_path", "/root/.ssh/id_rsa")
                try:
                    pk = _pm.Ed25519Key.from_private_key_file(kp)
                except Exception:
                    pk = None
                if pk:
                    cl.connect(ssh_cfg["host"], port=int(ssh_cfg.get("port") or 22),
                               username=ssh_cfg.get("user", "root"), pkey=pk,
                               timeout=15, allow_agent=False, look_for_keys=False)
                else:
                    cl.connect(ssh_cfg["host"], port=int(ssh_cfg.get("port") or 22),
                               username=ssh_cfg.get("user", "root"), key_filename=kp, timeout=15)
                if not source[:1].isalpha():
                    return
                drive_letter = source[0].upper()
                _, out, _ = cl.exec_command(
                    f'powershell -NonInteractive -Command "(Get-PSDrive {drive_letter}).Used"')
                val = out.read().decode("utf-8", errors="replace").strip().replace(",", ".")
                total = int(float(val)) if val else 0
                cl.close()
                if total > 0 and isinstance(_RUNNING_JOBS.get(jid), dict):
                    _RUNNING_JOBS[jid]["bytes_total"] = total
            except Exception:
                pass
        threading.Thread(target=_fetch_total_ssh, daemon=True).start()
        threading.Thread(
            target=_do_ssh_robocopy,
            args=(jid, ssh_cfg, source, destination, job.get("name", "sin nombre")),
            daemon=True,
        ).start()
        return {"ok": True, "running": True, "message": f"Backup SSH iniciado: {job.get('name')}"}

    # Modo local (rsync interno NAS, sin SMB, con -x y exclusiones)
    if mode == "local":
        local_cfg = job.get("local") if isinstance(job.get("local"), dict) else {}
        raw_excludes = str(local_cfg.get("excludes") or "")
        excludes = [e.strip() for e in raw_excludes.splitlines() if e.strip()]
        if not Path(source).exists():
            return {"error": f"Origen no existe: {source}"}
        if not Path(destination).exists():
            try:
                Path(destination).mkdir(parents=True, exist_ok=True)
            except Exception as e:
                return {"error": f"Destino no existe y no se pudo crear: {destination} ({e})"}
        _RUNNING_JOBS[jid] = {
            "start_time": time.time(),
            "df_free_start": _get_df_free(destination),
            "dest_path": destination,
            "bytes_total": None,
        }
        threading.Thread(
            target=_do_rsync,
            args=(jid, source, destination, job.get("name", "sin nombre")),
            kwargs={"excludes": excludes, "one_fs": True},
            daemon=True,
        ).start()
        return {"ok": True, "running": True, "message": f"Backup local iniciado: {job.get('name')}"}

    # Modo rsync
    smb = job.get("smb") if isinstance(job.get("smb"), dict) else None
    smb_active = smb and smb.get("host") and smb.get("mountpoint")
    if not smb_active and not Path(source).exists():
        return {"error": f"Origen no existe: {source}"}
    if not Path(destination).exists():
        try:
            Path(destination).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return {"error": f"Destino no existe y no se pudo crear: {destination} ({e})"}

    rsync_excl_raw = str((smb or {}).get("excludes") or "")
    rsync_excludes = [l.strip() for l in rsync_excl_raw.splitlines() if l.strip()]
    _RUNNING_JOBS[jid] = {
        "start_time": time.time(),
        "df_free_start": _get_df_free(destination),
        "dest_path": destination,
        "bytes_total": None,
    }
    threading.Thread(
        target=_do_rsync,
        args=(jid, source, destination, job.get("name", "sin nombre"), smb),
        kwargs={"excludes": rsync_excludes},
        daemon=True,
    ).start()

    return {"ok": True, "running": True, "message": f"Backup iniciado: {job.get('name')}"}


# ---------------------------------------------------------------------------
# Scheduler automático
# ---------------------------------------------------------------------------

def _should_run_now(job):
    """Devuelve True si el job debe ejecutarse ahora.
    Incluye catch-up: si la hora programada ya pasó hoy y no se ha ejecutado
    en el periodo actual, lo lanza aunque no sea el minuto exacto."""
    if not _to_bool(job.get("enabled"), True):
        return False
    sched = job.get("schedule") or {}
    freq = str(sched.get("frequency") or "daily")
    sched_time = str(sched.get("time") or "03:00")

    now = time.localtime()
    now_hhmm = f"{now.tm_hour:02d}:{now.tm_min:02d}"

    # Solo actuar si la hora programada ya llegó o pasó
    if now_hhmm < sched_time:
        return False

    last = str(job.get("last_backup") or "")
    if not last:
        return True  # Nunca ha corrido: lanzar si la hora programada ya llegó
    try:
        lt = time.strptime(last, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return False

    same_period = False
    if freq == "daily":
        same_period = (now.tm_year, now.tm_mon, now.tm_mday) == (lt.tm_year, lt.tm_mon, lt.tm_mday)
    elif freq == "weekly":
        same_period = time.strftime("%Y-%W", now) == time.strftime("%Y-%W", lt)
    elif freq == "monthly":
        same_period = (now.tm_year, now.tm_mon) == (lt.tm_year, lt.tm_mon)
    if not same_period:
        return True
    # Si corrió en este periodo pero con error, reintentar solo si pasó >1h
    if str(job.get("status") or "") == "err":
        try:
            return (time.time() - time.mktime(lt)) > 3600
        except Exception:
            return False
    return False


def start_scheduler():
    """Arranca el hilo del scheduler (llamar una sola vez al iniciar la app)."""
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True

    def _loop():
        while True:
            time.sleep(60)
            try:
                store = _load_store()
                for job in store.get("jobs", []):
                    jid = job.get("id")
                    if not jid or jid in _RUNNING_JOBS or not _should_run_now(job):
                        continue
                    try:
                        result = run_backup(jid)
                    except Exception as exc:
                        result = {"error": f"Excepción inesperada: {exc}"}
                    if isinstance(result, dict) and result.get("error"):
                        _fail_job(jid, job.get("name", jid), result["error"])
            except Exception:
                pass
            # Apagado programado: lo gestiona el panel para no cortar un backup en marcha.
            try:
                from system.power import check_scheduled_shutdown
                check_scheduled_shutdown(lambda: bool(_RUNNING_JOBS))
            except Exception:
                pass

    threading.Thread(target=_loop, daemon=True).start()

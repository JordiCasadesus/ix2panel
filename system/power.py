"""Módulo de gestión de apagado programado para ix2panel.

El apagado lo hace el propio panel (check_scheduled_shutdown, llamado cada minuto por el
planificador de backups) y NO el cron de root: así puede esperar a que acabe un backup en marcha.
"""
import json
import os
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

SCHEDULE_FILE = Path("/var/lib/ix2panel/power_schedule.json")

# Si a la hora del apagado hay un backup corriendo, se espera hasta este tope (minutos).
MAX_WAIT_MIN = 180

_BOOT = datetime.now()   # momento en que arrancó el panel
_STATE = {}              # estado del apagado en curso (en memoria)
_CRON_CLEANED = False

def _load_schedule():
    if SCHEDULE_FILE.exists():
        try:
            return json.loads(SCHEDULE_FILE.read_text())
        except Exception:
            pass
    return {"enabled": False, "shutdown_time": "02:00", "days": "all"}

def _save_schedule(data):
    SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
    SCHEDULE_FILE.write_text(json.dumps(data, indent=2))
    try:
        os.chmod(SCHEDULE_FILE, 0o600)
    except Exception:
        pass

def get_power_status():
    s = _load_schedule()
    next_shutdown = "—"
    if s.get("enabled"):
        now = datetime.now()
        t = s.get("shutdown_time", "02:00")
        h, m = int(t.split(":")[0]), int(t.split(":")[1])
        candidate = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        next_shutdown = candidate.strftime("%Y-%m-%d %H:%M")

    return {
        "enabled": s.get("enabled", False),
        "shutdown_time": s.get("shutdown_time", "02:00"),
        "days": s.get("days", "all"),
        "next_shutdown": next_shutdown,
    }

def set_schedule(enabled, shutdown_time, days):
    shutdown_time = str(shutdown_time).strip()
    days = str(days).strip()
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", shutdown_time):
        return {"error": "Hora inválida (HH:MM)"}
    if days not in ("all", "workdays"):
        return {"error": "Días inválidos"}
    data = {
        "enabled": bool(enabled),
        "shutdown_time": str(shutdown_time),
        "days": str(days),
    }
    _save_schedule(data)
    _remove_legacy_cron()
    _STATE.clear()   # un cambio de programación empieza de cero
    return {"ok": True}

def _remove_legacy_cron():
    """Quita del cron de root la línea antigua de apagado (ahora lo hace el panel).
    Solo toca el crontab si esa línea existe."""
    try:
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        lines = r.stdout.splitlines()
        kept = [l for l in lines if "ix2panel-shutdown" not in l]
        if len(kept) == len(lines):
            return
        proc = subprocess.Popen(["crontab", "-"], stdin=subprocess.PIPE, text=True)
        proc.communicate("\n".join(kept) + "\n" if kept else "")
    except Exception:
        pass

def _do_shutdown():
    try:
        subprocess.Popen(["/sbin/shutdown", "-h", "now"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

def _notify(msg):
    try:
        from system.notifications import send
        send(msg)
    except Exception:
        pass

def check_scheduled_shutdown(is_backup_running, now=None, shutdown_fn=None, notify_fn=None):
    """Llamar cada minuto. Apaga a la hora programada, salvo que haya un backup corriendo:
    entonces espera (hasta MAX_WAIT_MIN) y avisa una vez. Devuelve un texto con lo decidido."""
    global _CRON_CLEANED
    if not _CRON_CLEANED:
        _CRON_CLEANED = True
        _remove_legacy_cron()

    s = _load_schedule()
    if not s.get("enabled"):
        return "desactivado"
    now = now or datetime.now()
    shutdown_fn = shutdown_fn or _do_shutdown
    notify_fn = notify_fn or _notify
    try:
        h, m = [int(x) for x in str(s.get("shutdown_time", "02:00")).split(":")]
    except Exception:
        return "hora invalida"

    # La hora de hoy o la de ayer (si la espera cruza la medianoche).
    for back in (0, 1):
        target = (now - timedelta(days=back)).replace(hour=h, minute=m, second=0, microsecond=0)
        if target > now:
            continue
        if s.get("days") == "workdays" and target.weekday() >= 5:
            continue
        waited = (now - target).total_seconds() / 60
        if waited > MAX_WAIT_MIN + 5:
            continue
        return _handle(target, waited, is_backup_running, shutdown_fn, notify_fn)
    return "fuera de hora"

def _handle(target, waited, is_backup_running, shutdown_fn, notify_fn):
    key = target.strftime("%Y-%m-%d %H:%M")
    if _STATE.get("key") != key:
        _STATE.clear()
        _STATE["key"] = key
    if _STATE.get("done"):
        return "hecho"
    # Si el panel arrancó después de la hora (NAS recién encendido, panel reiniciado), no apagar.
    if _BOOT > target + timedelta(minutes=2):
        _STATE["done"] = True
        return "panel arrancado tarde"
    if waited > MAX_WAIT_MIN:
        _STATE["done"] = True
        if _STATE.get("notified"):
            notify_fn("⚠️ Apagado programado omitido: sigue habiendo un backup en marcha tras "
                      + str(MAX_WAIT_MIN // 60) + " h. El NAS sigue encendido.")
        return "omitido"
    if is_backup_running():
        if not _STATE.get("notified"):
            _STATE["notified"] = True
            notify_fn("⏳ Apagado programado pospuesto: hay un backup en marcha. "
                      "Se apagará al terminar (máx. " + str(MAX_WAIT_MIN // 60) + " h).")
        return "pospuesto"
    _STATE["done"] = True
    shutdown_fn()
    return "apagando"

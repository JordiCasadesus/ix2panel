"""SMART disk health via smartctl."""
import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

SCHEDULE_FILE = Path("/var/lib/ix2panel/smart_schedule.json")

_DEFAULT_SCHEDULE = {
    "enabled": False,
    "weekday": 5,        # 0=lunes ... 6=domingo
    "time": "10:00",
    "last_run": "",
}

_DEVICE_RE = re.compile(r"^/dev/(sd[a-z]+|hd[a-z]+|vd[a-z]+|nvme\d+n\d+|mmcblk\d+)$")


def valid_device(device):
    """Solo discos fisicos reales (/dev/sda, /dev/nvme0n1...), nunca particiones ni rutas raras."""
    return bool(_DEVICE_RE.match(str(device)))


WEEKDAY_NAMES = ["lunes", "martes", "miércoles", "jueves",
                 "viernes", "sábado", "domingo"]

def get_smart(device):
    """Devuelve datos SMART básicos para un dispositivo (ej: /dev/sda)."""
    result = {
        "device": device,
        "available": False,
        "health": "—",
        "model": "—",
        "serial": "—",
        "hours": "—",
        "temp": "—",
        "reallocated": "—",
        "pending": "—",
        "uncorrectable": "—",
        "rpm": "—",
    }
    if not valid_device(device):
        result["error"] = "dispositivo invalido"
        return result
    try:
        r = subprocess.run(
            ["/usr/sbin/smartctl", "-a", device],
            capture_output=True, text=True, timeout=15
        )
        out = r.stdout
        if not out:
            return result
        result["available"] = True

        m = re.search(r"SMART overall-health.*?:\s*(\w+)", out)
        if m: result["health"] = m.group(1)

        m = re.search(r"Device Model:\s+(.+)", out)
        if m: result["model"] = m.group(1).strip()

        m = re.search(r"Serial Number:\s+(\S+)", out)
        if m: result["serial"] = m.group(1)

        m = re.search(r"Rotation Rate:\s+(\d+)\s+rpm", out)
        if m: result["rpm"] = m.group(1) + " rpm"

        for line in out.splitlines():
            parts = line.split()
            if len(parts) < 10: continue
            name = parts[1]
            val = parts[9]
            if name == "Power_On_Hours":       result["hours"] = val + " h"
            elif name in ("Temperature_Celsius", "Airflow_Temperature_Cel"):
                result["temp"] = val.split("(")[0].strip() + " °C"
            elif name == "Reallocated_Sector_Ct": result["reallocated"] = val
            elif name == "Current_Pending_Sector": result["pending"] = val
            elif name == "Offline_Uncorrectable":  result["uncorrectable"] = val

    except Exception as e:
        result["error"] = str(e)
    return result


# ---------------------------------------------------------------------------
# Discos fisicos
# ---------------------------------------------------------------------------

def physical_disks():
    """Discos fisicos del sistema segun lsblk (/dev/sda, /dev/sdb...)."""
    try:
        r = subprocess.run(["lsblk", "-dn", "-o", "NAME,TYPE"],
                           capture_output=True, text=True, timeout=10)
        devs = []
        for line in r.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "disk":
                devs.append("/dev/" + parts[0])
        return devs
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Test largo (extended self-test)
# ---------------------------------------------------------------------------

def get_test_progress(device):
    """Estado del autotest de un disco: en curso, % restante y ultimo resultado."""
    info = {"running": False, "remaining": "", "last_result": "—", "last_hours": ""}
    try:
        r = subprocess.run(["/usr/sbin/smartctl", "-c", "-l", "selftest", device],
                           capture_output=True, text=True, timeout=20)
        out = r.stdout

        if "in progress" in out:
            info["running"] = True
            m = re.search(r"(\d+)%\s+of test remaining", out)
            if m:
                info["remaining"] = m.group(1) + "%"

        # Primera fila del historial de autotests
        for line in out.splitlines():
            if line.startswith("#") and "%" in line:
                m = re.match(r"#\s*\d+\s+(.+?)\s{2,}(.+?)\s{2,}\d+%\s+(\d+)", line)
                if m:
                    info["last_result"] = m.group(1).strip() + " - " + m.group(2).strip()
                    info["last_hours"] = m.group(3) + " h"
                break
    except Exception:
        pass
    return info


def start_long_test(device=""):
    """Lanza el test largo en un disco, o en todos si no se indica ninguno."""
    devs = [device] if device else physical_disks()
    if not devs:
        return {"ok": False, "error": "No se han encontrado discos"}

    results = {}
    for d in devs:
        if not valid_device(d):
            results[d] = "dispositivo invalido"
            continue
        try:
            r = subprocess.run(["/usr/sbin/smartctl", "-t", "long", d],
                               capture_output=True, text=True, timeout=30)
            if "has begun" in r.stdout or r.returncode == 0:
                results[d] = "iniciado"
            else:
                lines = [l for l in r.stdout.strip().splitlines() if l.strip()]
                results[d] = lines[-1] if lines else "error desconocido"
        except Exception as e:
            results[d] = str(e)
    return {"ok": True, "results": results}


# ---------------------------------------------------------------------------
# Programacion semanal
# ---------------------------------------------------------------------------

def _load_schedule():
    if SCHEDULE_FILE.exists():
        try:
            data = dict(_DEFAULT_SCHEDULE)
            data.update(json.loads(SCHEDULE_FILE.read_text()))
            return data
        except Exception:
            pass
    return dict(_DEFAULT_SCHEDULE)


def _save_schedule(data):
    SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
    SCHEDULE_FILE.write_text(json.dumps(data, indent=2))
    try:
        os.chmod(SCHEDULE_FILE, 0o600)
    except Exception:
        pass


def _scheduled_dt(schedule, now):
    """Momento programado dentro de la semana de 'now' (lunes como inicio)."""
    wd = int(schedule.get("weekday", 5))
    hh, mm = [int(x) for x in str(schedule.get("time", "10:00")).split(":")]
    week_start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return week_start + timedelta(days=wd, hours=hh, minutes=mm)


def _is_due(schedule, now):
    """True si toca lanzar el test y aun no se ha hecho esta semana.

    Incluye recuperacion: si la hora paso con el NAS apagado, se lanza en
    cuanto el panel vuelva a estar en marcha dentro de la misma semana.
    """
    if not schedule.get("enabled"):
        return False
    try:
        scheduled = _scheduled_dt(schedule, now)
    except Exception:
        return False
    if now < scheduled:
        return False
    last = schedule.get("last_run", "")
    if last:
        try:
            if datetime.strptime(last, "%Y-%m-%d %H:%M:%S") >= scheduled:
                return False
        except Exception:
            pass
    return True


def get_schedule():
    """Configuracion, proxima ejecucion y estado del autotest de cada disco."""
    s = _load_schedule()
    now = datetime.now()

    next_run = "—"
    if s.get("enabled"):
        try:
            scheduled = _scheduled_dt(s, now)
            if _is_due(s, now):
                next_run = "pendiente (se lanzara en breve)"
            else:
                if now >= scheduled:
                    scheduled += timedelta(days=7)
                next_run = scheduled.strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass

    disks = []
    for d in physical_disks():
        prog = get_test_progress(d)
        prog["device"] = d
        disks.append(prog)

    return {
        "enabled": s.get("enabled", False),
        "weekday": s.get("weekday", 5),
        "time": s.get("time", "10:00"),
        "last_run": s.get("last_run", "") or "—",
        "next_run": next_run,
        "disks": disks,
    }


def set_schedule(enabled, weekday, time_str):
    s = _load_schedule()
    try:
        wd = int(weekday)
    except (TypeError, ValueError):
        return {"ok": False, "error": "Dia invalido"}
    if not 0 <= wd <= 6:
        return {"ok": False, "error": "Dia invalido"}
    if not re.match(r"^\d{1,2}:\d{2}$", str(time_str or "")):
        return {"ok": False, "error": "Hora invalida"}
    hh, mm = [int(x) for x in str(time_str).split(":")]
    if hh > 23 or mm > 59:
        return {"ok": False, "error": "Hora invalida"}

    s["enabled"] = bool(enabled)
    s["weekday"] = wd
    s["time"] = "%02d:%02d" % (hh, mm)
    _save_schedule(s)
    return {"ok": True}


_scheduler_started = False


def _scheduler_loop():
    time.sleep(60)   # margen tras el arranque
    while True:
        try:
            s = _load_schedule()
            if _is_due(s, datetime.now()):
                start_long_test("")
                s["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                _save_schedule(s)
        except Exception:
            pass
        time.sleep(300)   # cada 5 minutos


def start_scheduler():
    """Arranca el hilo que lanza el test largo programado."""
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True
    threading.Thread(target=_scheduler_loop, daemon=True).start()

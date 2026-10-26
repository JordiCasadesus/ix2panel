import platform
import subprocess
import time
from datetime import datetime

# =========================    SERVICES
SERVICES = [
    {"label": "SSH", "linux": "ssh", "windows": "ssh", "port": 22, "proto": "TCP"},
    {"label": "SMB", "linux": "smbd", "windows": "LanmanServer", "port": 445, "proto": "TCP"},
    {"label": "Web", "linux": "nginx", "windows": "W3SVC", "port": 80, "proto": "TCP"},
    {"label": "WireGuard", "linux": "wg-quick@wg0", "windows": None, "port": 51820, "proto": "UDP"},
    {"label": "DDNS", "linux": "ddclient", "windows": None, "port": None, "proto": None},
    {"label": "Firewall", "linux": "ufw", "windows": None, "port": None, "proto": None, "type": "firewall"},
]
def get_services_status():
    result = []
    is_win = platform.system().lower() == "windows"

    for s in SERVICES:
        name = s["label"]
        svc = s["windows"] if is_win else s["linux"]

        if not svc:
            result.append({"name": name, "status": "warn", "unit": None, "port": s.get("port"), "proto": s.get("proto")})
            continue

        try:
            if is_win:
                out = subprocess.check_output(["sc", "query", svc], text=True)
                ok = "RUNNING" in out
            else:
                subprocess.check_call(
                    ["systemctl", "is-active", "--quiet", svc]
                )
                ok = True

            result.append({"name": name, "status": "ok" if ok else "err", "unit": svc, "port": s.get("port"), "proto": s.get("proto"), "type": s.get("type", "")})
        except Exception:
            result.append({"name": name, "status": "err", "unit": svc, "port": s.get("port"), "proto": s.get("proto")})

    return result


def _find_service(label):
    for s in SERVICES:
        if s["label"].lower() == label.lower():
            return s
    return None


def get_service_detail(label):
    svc = _find_service(label)
    if not svc:
        return {"error": "Servicio no encontrado"}

    unit = svc["linux"]
    try:
        out = subprocess.check_output(
            ["systemctl", "show", unit, "-p", "ActiveState,UnitFileState,ActiveEnterTimestampUSec,ActiveEnterTimestamp"],
            text=True,
            errors="replace"
        )
    except Exception as e:
        return {"error": str(e)}

    data = {}
    for line in out.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            data[k] = v

    active = data.get("ActiveState", "unknown")
    unit_state = data.get("UnitFileState", "unknown")
    ts_us = data.get("ActiveEnterTimestampUSec", "0")
    ts_str = data.get("ActiveEnterTimestamp", "")
    try:
        ts = int(ts_us) / 1_000_000
        if ts == 0 and ts_str:
            import subprocess as _sp
            r2 = _sp.run(["date", "-d", ts_str, "+%s"], capture_output=True, text=True, timeout=3)
            ts = float(r2.stdout.strip()) if r2.stdout.strip().isdigit() else 0
        last_restart = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else "—"
        uptime_sec = max(0, int(time.time() - ts)) if ts else 0
        uptime = f"{uptime_sec // 3600}h {((uptime_sec % 3600) // 60)}m" if ts else "—"
    except Exception:
        last_restart = "—"
        uptime = "—"

    return {
        "name": svc["label"],
        "unit": unit,
        "status": "ok" if active == "active" else "err",
        "autostart": "enabled" in unit_state,
        "port": svc.get("port"),
        "proto": svc.get("proto"),
        "last_restart": last_restart,
        "uptime": uptime
    }


def restart_service(label):
    svc = _find_service(label)
    if not svc:
        return {"error": "Servicio no encontrado"}
    try:
        subprocess.check_output(["sudo", "systemctl", "restart", svc["linux"]], text=True, errors="replace")
        return {"ok": True}
    except Exception as e:
        return {"error": str(e)}


def stop_service(label):
    svc = _find_service(label)
    if not svc:
        return {"error": "Servicio no encontrado"}
    try:
        subprocess.check_output(["sudo", "systemctl", "stop", svc["linux"]], text=True, errors="replace")
        return {"ok": True}
    except Exception as e:
        return {"error": str(e)}


def enable_firewall():
    try:
        subprocess.check_output(["ufw", "--force", "enable"], text=True, stderr=subprocess.STDOUT)
        return {"ok": True}
    except Exception as e:
        return {"error": str(e)}


def disable_firewall():
    try:
        subprocess.check_output(["ufw", "disable"], text=True, stderr=subprocess.STDOUT)
        return {"ok": True}
    except Exception as e:
        return {"error": str(e)}

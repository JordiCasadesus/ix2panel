"""
Notificaciones Telegram para ix2panel.
Config guardada en /var/lib/ix2panel/notifications.json
"""
import ipaddress
import json
import os
import urllib.request
import urllib.parse
import urllib.error
import time
import threading
from pathlib import Path

_CONFIG_PATH = Path("/var/lib/ix2panel/notifications.json")

_DEFAULTS = {
    "enabled": False,
    "token": "",
    "chat_id": "",
    "events": {
        "backup_ok":     True,
        "backup_err":    True,
        "nas_start":     True,
        "service_down":  True,
        "disk_full":     True,
        "login_ok":      False,
        "login_fail":    True,
        "temp_high":     True,
        "ram_low":       True,
        "ip_change":     True,
        "backup_stale":  True,
        "wg_peer":       True,
        "smart_fail":    True,
    }
}

# Estado interno del monitor (en memoria)
_monitor_state = {
    "last_ip":    None,
    "wg_active":  None,   # set de peers activos en el ultimo check (None = primera vez)
    "cooldown":   {},     # event_key -> last_sent epoch
}
_COOLDOWN_SEC = 3600      # 1 hora entre avisos del mismo tipo


def _load():
    try:
        if _CONFIG_PATH.exists():
            data = json.loads(_CONFIG_PATH.read_text())
            cfg = dict(_DEFAULTS)
            cfg.update(data)
            cfg["events"] = dict(_DEFAULTS["events"])
            cfg["events"].update(data.get("events", {}))
            return cfg
    except Exception:
        pass
    return dict(_DEFAULTS)


def _save(cfg):
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    try:
        os.chmod(_CONFIG_PATH, 0o600)
    except Exception:
        pass


def get_config():
    return _load()


def save_config(data):
    cfg = _load()
    if "enabled" in data:
        cfg["enabled"] = bool(data["enabled"])
    if "token" in data:
        cfg["token"] = str(data["token"]).strip()
    if "chat_id" in data:
        cfg["chat_id"] = str(data["chat_id"]).strip()
    if "events" in data:
        for k, v in data["events"].items():
            if k in cfg["events"]:
                cfg["events"][k] = bool(v)
    _save(cfg)
    return {"ok": True}


def send(message: str, event: str = None) -> bool:
    """Envia un mensaje Telegram. Devuelve True si tuvo exito."""
    cfg = _load()
    if not cfg["enabled"]:
        return False
    if event and not cfg["events"].get(event, True):
        return False
    token = cfg.get("token", "").strip()
    chat_id = cfg.get("chat_id", "").strip()
    if not token or not chat_id:
        return False
    try:
        url = "https://api.telegram.org/bot" + token + "/sendMessage"
        payload = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
        }).encode()
        req = urllib.request.Request(url, data=payload, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            return result.get("ok", False)
    except Exception:
        return False


def _cooldown_ok(key):
    """Devuelve True si no se ha enviado este aviso en la ultima hora."""
    last = _monitor_state["cooldown"].get(key, 0)
    if (time.time() - last) > _COOLDOWN_SEC:
        _monitor_state["cooldown"][key] = time.time()
        return True
    return False


def test_notification():
    """Envia mensaje de prueba ignorando el flag enabled."""
    cfg = _load()
    token = cfg.get("token", "").strip()
    chat_id = cfg.get("chat_id", "").strip()
    if not token or not chat_id:
        return {"ok": False, "error": "Token o chat_id no configurados"}
    try:
        url = "https://api.telegram.org/bot" + token + "/sendMessage"
        payload = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": "Prueba ix2Panel NAS - Notificaciones configuradas correctamente.",
            "parse_mode": "HTML",
        }).encode()
        req = urllib.request.Request(url, data=payload, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if result.get("ok"):
                return {"ok": True}
            return {"ok": False, "error": str(result)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Monitor: temperatura, RAM, IP publica, backups sin ejecutar, WireGuard
# ---------------------------------------------------------------------------

def _check_temperature():
    try:
        raw = Path("/sys/class/thermal/thermal_zone0/temp").read_text().strip()
        temp_c = int(raw) / 1000
        if temp_c >= 75 and _cooldown_ok("temp_high"):
            send("Temperatura alta - NAS: " + str(round(temp_c, 1)) + "C", event="temp_high")
    except Exception:
        pass


def _check_ram():
    try:
        meminfo = Path("/proc/meminfo").read_text()
        avail = None
        for line in meminfo.splitlines():
            if line.startswith("MemAvailable:"):
                avail = int(line.split()[1])  # kB
                break
        if avail is not None and avail < 30000 and _cooldown_ok("ram_low"):
            send("RAM critica - Disponible: " + str(round(avail / 1024, 1)) + " MB", event="ram_low")
    except Exception:
        pass


def _check_public_ip():
    try:
        req = urllib.request.Request("https://ipinfo.io/ip",
                                     headers={"User-Agent": "ix2panel/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            ip = resp.read().decode().strip()
        if not ip:
            return
        ipaddress.ip_address(ip)  # una respuesta que no sea una IP (error, basura) no debe disparar el aviso
        last = _monitor_state.get("last_ip")
        if last is None:
            _monitor_state["last_ip"] = ip
            return
        if ip != last:
            _monitor_state["last_ip"] = ip
            if _cooldown_ok("ip_change"):
                send("IP publica cambiada - Anterior: " + last + " Nueva: " + ip, event="ip_change")
    except Exception:
        pass


def _check_backup_stale():
    try:
        store_path = Path("/var/lib/ix2panel/backup.json")
        if not store_path.exists():
            return
        store = json.loads(store_path.read_text())
        now = time.time()
        freq_max = {"daily": 2, "weekly": 9, "monthly": 33}
        for job in store.get("jobs", []):
            if not job.get("enabled", True):
                continue
            if job.get("status") == "err":
                continue  # ya avisa backup_err
            freq = (job.get("schedule") or {}).get("frequency", "daily")
            max_days = freq_max.get(freq, 2)
            last = job.get("last_backup", "")
            if not last:
                continue
            try:
                lt = time.mktime(time.strptime(last, "%Y-%m-%d %H:%M:%S"))
            except Exception:
                continue
            days_ago = (now - lt) / 86400
            if days_ago > max_days:
                key = "backup_stale_" + str(job.get("id", job.get("name", "")))
                if _cooldown_ok(key):
                    name = job.get("name", "desconocido")
                    send("Backup sin ejecutar - " + name + " - Ultimo hace " + str(int(days_ago)) + " dias", event="backup_stale")
    except Exception:
        pass


def _check_smart():
    """Avisa si algun disco reporta fallo SMART o sectores defectuosos."""
    try:
        from system.smart import get_smart, physical_disks
    except Exception:
        return

    for dev in physical_disks():
        try:
            data = get_smart(dev)
        except Exception:
            continue
        if not data.get("available"):
            continue

        problems = []
        health = (data.get("health") or "").strip().upper()
        if health and health not in ("PASSED", "OK", "—"):
            problems.append("estado " + health)

        for key, label in (("reallocated",   "sectores realojados"),
                           ("pending",       "sectores pendientes"),
                           ("uncorrectable", "sectores no corregibles")):
            try:
                n = int(str(data.get(key, "")).strip())
            except (TypeError, ValueError):
                continue   # "—" cuando el disco no reporta ese atributo
            if n > 0:
                problems.append(label + ": " + str(n))

        if problems and _cooldown_ok("smart_" + dev):
            model = data.get("model", "")
            extra = " (" + model + ")" if model and model != "—" else ""
            send("Disco con problemas SMART - " + dev + extra +
                 " - " + ", ".join(problems), event="smart_fail")


def _wg_peer_names():
    try:
        data = json.loads(open('/etc/wireguard/panel_peers.json').read())
        return data
    except Exception:
        return {}

def _check_wireguard():
    try:
        import subprocess
        r = subprocess.run(["wg", "show", "wg0", "latest-handshakes"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return
        peer_names = _wg_peer_names()
        now = int(time.time())
        current_active = set()
        for line in r.stdout.strip().splitlines():
            parts = line.split()
            if len(parts) == 2:
                peer, ts = parts[0], int(parts[1])
                # Activo si handshake en los ultimos 3 minutos
                if ts > 0 and (now - ts) < 180:
                    current_active.add(peer)

        prev_active = _monitor_state["wg_active"]

        if prev_active is None:
            # Primera ejecucion: solo inicializar, no notificar
            _monitor_state["wg_active"] = current_active
            return

        # Peers que acaban de conectarse
        for peer in current_active - prev_active:
            if _cooldown_ok("wg_conn_" + peer):
                short = peer[:8] + "..."
                name = peer_names.get(peer, peer[:8] + "..."); send("WireGuard peer conectado: " + name, event="wg_peer")

        # Peers que se han desconectado
        for peer in prev_active - current_active:
            if _cooldown_ok("wg_disc_" + peer):
                short = peer[:8] + "..."
                name = peer_names.get(peer, peer[:8] + "..."); send("WireGuard peer desconectado: " + name, event="wg_peer")

        _monitor_state["wg_active"] = current_active
    except Exception:
        pass


def _monitor_loop():
    # Espera inicial para no solapar con el arranque
    time.sleep(30)
    tick = 0
    while True:
        try:
            _check_temperature()
            _check_ram()
            _check_wireguard()
            if tick % 12 == 0:   # cada hora (12 x 5 min)
                _check_public_ip()
                _check_backup_stale()
            if tick % 72 == 0:   # cada 6 horas (72 x 5 min)
                _check_smart()
        except Exception:
            pass
        tick += 1
        time.sleep(300)  # cada 5 minutos


_monitor_started = False


def start_monitor():
    """Arranca el hilo monitor (llamar una sola vez al iniciar la app)."""
    global _monitor_started
    if _monitor_started:
        return
    _monitor_started = True
    t = threading.Thread(target=_monitor_loop, daemon=True, name="notif-monitor")
    t.start()

import json
import socket
from pathlib import Path

_WOL_PATH = Path("/var/lib/ix2panel/wol_targets.json")
_DEFAULTS = []

def _load():
    try:
        if _WOL_PATH.exists():
            return json.loads(_WOL_PATH.read_text())
    except Exception:
        pass
    return list(_DEFAULTS)

def _save(targets):
    _WOL_PATH.parent.mkdir(parents=True, exist_ok=True)
    _WOL_PATH.write_text(json.dumps(targets, indent=2))

def get_wol_targets():
    return _load()

def add_wol_target(name, mac, ip=''):
    name = name.strip(); mac = mac.strip()
    if not name or not mac:
        return {"ok": False, "error": "Nombre y MAC son obligatorios"}
    try:
        mac_clean = mac.replace(':', '').replace('-', '')
        if len(mac_clean) != 12: raise ValueError
        int(mac_clean, 16)
    except Exception:
        return {"ok": False, "error": "MAC invalida"}
    targets = _load()
    if any(t['name'].lower() == name.lower() for t in targets):
        return {"ok": False, "error": "Ya existe un equipo con ese nombre"}
    targets.append({"name": name, "mac": mac.lower(), "ip": ip.strip()})
    _save(targets)
    return {"ok": True}

def edit_wol_target(old_name, name, mac, ip=''):
    old_name = (old_name or '').strip()
    name = (name or '').strip()
    mac = (mac or '').strip()
    if not old_name:
        return {"ok": False, "error": "Equipo no especificado"}
    if not name or not mac:
        return {"ok": False, "error": "Nombre y MAC son obligatorios"}
    try:
        mac_clean = mac.replace(':', '').replace('-', '')
        if len(mac_clean) != 12: raise ValueError
        int(mac_clean, 16)
    except Exception:
        return {"ok": False, "error": "MAC invalida"}
    targets = _load()
    idx = next((i for i, t in enumerate(targets) if t['name'] == old_name), None)
    if idx is None:
        return {"ok": False, "error": "Equipo no encontrado"}
    if name.lower() != old_name.lower() and any(t['name'].lower() == name.lower() for i, t in enumerate(targets) if i != idx):
        return {"ok": False, "error": "Ya existe un equipo con ese nombre"}
    targets[idx] = {"name": name, "mac": mac.lower(), "ip": ip.strip()}
    _save(targets)
    return {"ok": True}

def delete_wol_target(name):
    targets = _load()
    new = [t for t in targets if t['name'] != name]
    if len(new) == len(targets):
        return {"ok": False, "error": "No encontrado"}
    _save(new)
    return {"ok": True}

def _get_broadcast():
    """Devuelve el broadcast de la interfaz LAN (eth0), fallback a 255.255.255.255."""
    try:
        import subprocess
        out = subprocess.check_output(['ip', '-4', 'addr', 'show', 'eth0'], text=True)
        for part in out.split():
            if part.startswith('brd'):
                continue
            # formato: inet x.x.x.x/xx brd x.x.x.x
        import re
        m = re.search(r'brd\s+(\d+\.\d+\.\d+\.\d+)', out)
        if m:
            return m.group(1)
    except Exception:
        pass
    return '255.255.255.255'

def shutdown_target(name):
    targets = _load()
    t = next((x for x in targets if x['name'] == name), None)
    if not t:
        return {"ok": False, "error": "Equipo no encontrado"}
    ip = (t.get('ip') or '').strip()
    if not ip:
        return {"ok": False, "error": "Este equipo no tiene IP configurada"}
    try:
        import subprocess
        result = subprocess.run(
            ['ssh', '-i', '/root/.ssh/id_rsa',
             '-o', 'ConnectTimeout=5',
             '-o', 'StrictHostKeyChecking=no',
             '-o', 'BatchMode=yes',
             f'TU_USUARIO@{ip}',
             'shutdown /s /t 5'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return {"ok": True}
        return {"ok": False, "error": result.stderr.strip() or "Error al apagar"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def send_wol(mac):
    try:
        mac_bytes = bytes.fromhex(mac.replace(':', '').replace('-', ''))
        if len(mac_bytes) != 6:
            return {"ok": False, "error": "MAC invalida"}
        magic = bytes([0xff]) * 6 + mac_bytes * 16
        broadcast = _get_broadcast()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.sendto(magic, (broadcast, 9))
            s.sendto(magic, ('255.255.255.255', 9))  # doble envío por si acaso
        return {"ok": True, "broadcast": broadcast}
    except Exception as e:
        return {"ok": False, "error": str(e)}

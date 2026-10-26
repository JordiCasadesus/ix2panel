# system/devices.py
# =====================================================
# Traduce una IP al nombre de un dispositivo conocido, para mostrarlo en el panel
# (p.ej. "smbd[10.0.0.2]" -> "smbd[10.0.0.2] · Movil de Jordi").
# Fuentes: peers de WireGuard (nombre que se les puso en la pestana VPN) y equipos de Wake-on-LAN.
# Solo se usa para MOSTRAR: no cambia ningun dato ni se usa para decidir permisos.
# =====================================================

import json
import re
import time
from pathlib import Path

_WG_CONF = Path("/etc/wireguard/wg0.conf")
_WG_NAMES = Path("/etc/wireguard/panel_peers.json")      # {clave_publica: nombre}
_WOL_TARGETS = Path("/var/lib/ix2panel/wol_targets.json")  # [{name, mac, ip}]

_IP_RE = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?![\d.])")
_TTL = 20  # segundos: evita releer ficheros en cada refresco del panel
_cache = {"ts": 0.0, "map": {}}


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_map():
    out = {"127.0.0.1": "NAS (local)", "10.0.0.1": "NAS (VPN)"}

    # WireGuard: IP del peer (AllowedIPs) -> nombre asignado a su clave publica
    names = _read_json(_WG_NAMES)
    names = names if isinstance(names, dict) else {}
    try:
        text = _WG_CONF.read_text(encoding="utf-8")
    except Exception:
        text = ""
    peer = None
    peers = []
    for raw in text.splitlines():
        line = raw.strip()
        if line == "[Peer]":
            peer = {}
            peers.append(peer)
        elif line == "[Interface]":
            peer = None
        elif peer is not None and "=" in line:
            k, _, v = line.partition("=")
            peer[k.strip()] = v.strip()
    for p in peers:
        name = str(names.get(p.get("PublicKey", ""), "") or "").strip()
        ip = (p.get("AllowedIPs", "").split(",")[0].split("/")[0]).strip()
        if name and ip:
            out[ip] = name

    # Wake-on-LAN: equipos con IP (no pisa a los de WireGuard)
    targets = _read_json(_WOL_TARGETS)
    for t in targets if isinstance(targets, list) else []:
        try:
            ip = str(t.get("ip") or "").strip()
            name = str(t.get("name") or "").strip()
        except AttributeError:
            continue
        if ip and name and ip not in out:
            out[ip] = name
    return out


def ip_names():
    """{ip: nombre} de los dispositivos conocidos (con una pequena cache)."""
    now = time.time()
    if now - _cache["ts"] > _TTL:
        try:
            _cache["map"] = _load_map()
        except Exception:
            pass
        _cache["ts"] = now
    return _cache["map"]


def device_in_text(text):
    """Nombre del primer dispositivo conocido cuya IP aparece en el texto, o ''."""
    names = ip_names()
    for m in _IP_RE.finditer(text or ""):
        if m.group(1) in names:
            return names[m.group(1)]
    return ""


def annotate_text(text):
    """Pone el nombre junto a cada IP conocida: '10.0.0.2' -> '10.0.0.2 (Movil)'."""
    names = ip_names()
    if not text or not names:
        return text
    return _IP_RE.sub(lambda m: f"{m.group(1)} ({names[m.group(1)]})" if m.group(1) in names else m.group(0), text)

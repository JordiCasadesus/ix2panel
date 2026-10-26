"""
WireGuard management backend for ix2panel.
Manages /etc/wireguard/wg0.conf, peers and service state.
"""
import io
import json
import os
import re
import subprocess
from pathlib import Path

try:
    import qrcode
    from qrcode.image.svg import SvgPathImage as _SvgPathImage
    _QR_AVAILABLE = True
except ImportError:
    _QR_AVAILABLE = False


def _make_qr_svg(text: str) -> str:
    """Return inline SVG string for QR code, or empty string if qrcode not installed."""
    if not _QR_AVAILABLE:
        return ""
    try:
        qr = qrcode.QRCode(image_factory=_SvgPathImage, error_correction=qrcode.constants.ERROR_CORRECT_L)
        qr.add_data(text)
        qr.make(fit=True)
        img = qr.make_image()
        buf = io.BytesIO()
        img.save(buf)
        return buf.getvalue().decode("utf-8")
    except Exception:
        return ""

WG_CONF    = Path("/etc/wireguard/wg0.conf")
WG_IFACE   = "wg0"
WG_PORT    = 51820
VPN_SUBNET = "10.0.0.0/24"
SERVER_IP  = "10.0.0.1"
_PANEL_CFG = Path("/etc/wireguard/panel_config.json")
_PEERS_CFG = Path("/etc/wireguard/panel_peers.json")  # name mapping pubkey->name

# -------------------------------------------------------
# Helpers
# -------------------------------------------------------

def _run(cmd, check=False):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if check and r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or r.stdout.strip())
        return r
    except FileNotFoundError:
        raise RuntimeError(f"Comando no encontrado: {cmd[0]}")


def _read_panel_cfg():
    try:
        return json.loads(_PANEL_CFG.read_text())
    except Exception:
        return {}


def _write_panel_cfg(data: dict):
    _PANEL_CFG.write_text(json.dumps(data, indent=2))


def _read_peers_cfg() -> dict:
    """Returns {pubkey: name}"""
    try:
        return json.loads(_PEERS_CFG.read_text())
    except Exception:
        return {}


def _write_peers_cfg(data: dict):
    _PEERS_CFG.write_text(json.dumps(data, indent=2))


def _atomic_write(path: Path, text: str):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(text)
    os.chmod(tmp, 0o600)  # wg0.conf lleva la clave privada del servidor
    tmp.rename(path)


def _next_peer_ip(existing_ips: list[str]) -> str:
    """Find next free IP in 10.0.0.2–10.0.0.254"""
    used = set(existing_ips)
    used.add(SERVER_IP)
    for i in range(2, 255):
        ip = f"10.0.0.{i}"
        if ip not in used:
            return ip
    raise RuntimeError("No hay IPs disponibles en la subred VPN")


def _parse_wg_conf(text: str) -> dict:
    """Parse wg0.conf into {'interface': {...}, 'peers': [...]}"""
    result = {"interface": {}, "peers": []}
    current = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line == "[Interface]":
            current = result["interface"]
        elif line == "[Peer]":
            peer = {}
            result["peers"].append(peer)
            current = peer
        elif "=" in line and current is not None:
            k, _, v = line.partition("=")
            current[k.strip()] = v.strip()
    return result


def _build_wg_conf(interface: dict, peers: list[dict]) -> str:
    lines = ["[Interface]"]
    for k, v in interface.items():
        lines.append(f"{k} = {v}")
    for peer in peers:
        lines.append("")
        lines.append("[Peer]")
        for k, v in peer.items():
            lines.append(f"{k} = {v}")
    return "\n".join(lines) + "\n"


# -------------------------------------------------------
# Public API
# -------------------------------------------------------

def get_status() -> dict:
    """Returns {running, server_pubkey, endpoint, peers: [...]}"""
    cfg = _read_panel_cfg()
    names = _read_peers_cfg()

    running = False
    raw_peers = []

    r = _run(["wg", "show", WG_IFACE])
    if r.returncode == 0:
        running = True
        # Parse peers from `wg show`
        current_peer = None
        for line in r.stdout.splitlines():
            m_peer = re.match(r"^\s*peer:\s+(.+)$", line)
            m_ep   = re.match(r"^\s*endpoint:\s+(.+)$", line)
            m_ip   = re.match(r"^\s*allowed ips:\s+(.+)$", line)
            m_hs   = re.match(r"^\s*latest handshake:\s+(.+)$", line)
            m_tx   = re.match(r"^\s*transfer:\s+(.+)$", line)
            if m_peer:
                current_peer = {"pubkey": m_peer.group(1).strip(), "endpoint": "", "allowed_ips": "", "handshake": "", "transfer": ""}
                raw_peers.append(current_peer)
            elif current_peer:
                if m_ep:   current_peer["endpoint"]     = m_ep.group(1).strip()
                if m_ip:   current_peer["allowed_ips"]  = m_ip.group(1).strip()
                if m_hs:   current_peer["handshake"]    = m_hs.group(1).strip()
                if m_tx:   current_peer["transfer"]     = m_tx.group(1).strip()
    else:
        # Not running — read peers from conf
        if WG_CONF.exists():
            try:
                parsed = _parse_wg_conf(WG_CONF.read_text())
                for p in parsed["peers"]:
                    raw_peers.append({
                        "pubkey": p.get("PublicKey", ""),
                        "endpoint": "",
                        "allowed_ips": p.get("AllowedIPs", ""),
                        "handshake": "",
                        "transfer": "",
                    })
            except Exception:
                pass

    peers_out = []
    for p in raw_peers:
        pk = p.get("pubkey", "")
        peers_out.append({
            "pubkey":      pk,
            "name":        names.get(pk, ""),
            "endpoint":    p.get("endpoint", ""),
            "allowed_ips": p.get("allowed_ips", ""),
            "handshake":   p.get("handshake", ""),
            "transfer":    p.get("transfer", ""),
        })

    # Server pubkey
    server_pubkey = ""
    priv_path = Path("/etc/wireguard/server_private.key")
    if priv_path.exists():
        try:
            priv = priv_path.read_text().strip()
            r2 = subprocess.run(["wg", "pubkey"], input=priv, capture_output=True, text=True, timeout=5)
            if r2.returncode == 0:
                server_pubkey = r2.stdout.strip()
        except Exception:
            pass

    return {
        "running":       running,
        "server_pubkey": server_pubkey,
        "endpoint":      cfg.get("endpoint", ""),
        "peers":         peers_out,
        "initialized":   WG_CONF.exists(),
    }


def init_server(endpoint: str) -> dict:
    """Create wg0.conf from server_private.key and enable service."""
    priv_path = Path("/etc/wireguard/server_private.key")
    if not priv_path.exists():
        return {"error": "No se encuentra /etc/wireguard/server_private.key"}

    priv = priv_path.read_text().strip()
    r = subprocess.run(["wg", "pubkey"], input=priv, capture_output=True, text=True, timeout=5)
    if r.returncode != 0:
        return {"error": "No se pudo derivar la clave publica"}

    # Enable IP forwarding
    try:
        subprocess.run(["sysctl", "-w", "net.ipv4.ip_forward=1"], check=True, capture_output=True)
        # Persist
        fwd_file = Path("/etc/sysctl.d/99-wireguard.conf")
        if not fwd_file.exists():
            fwd_file.write_text("net.ipv4.ip_forward = 1\n")
    except Exception:
        pass

    conf_text = f"""[Interface]
PrivateKey = {priv}
Address = {SERVER_IP}/24
ListenPort = {WG_PORT}
PostUp = iptables -A FORWARD -i {WG_IFACE} -j ACCEPT; iptables -A FORWARD -o {WG_IFACE} -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i {WG_IFACE} -j ACCEPT; iptables -D FORWARD -o {WG_IFACE} -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE
"""
    try:
        _atomic_write(WG_CONF, conf_text)
        os.chmod(WG_CONF, 0o600)
    except Exception as e:
        return {"error": f"No se pudo escribir wg0.conf: {e}"}

    # Enable systemd service
    subprocess.run(["systemctl", "enable", "wg-quick@wg0"], capture_output=True)

    # Save endpoint
    save_endpoint(endpoint)

    return {"ok": True}


def save_endpoint(endpoint: str) -> dict:
    cfg = _read_panel_cfg()
    cfg["endpoint"] = endpoint
    try:
        _write_panel_cfg(cfg)
    except Exception as e:
        return {"error": str(e)}
    return {"ok": True}


def start_wg() -> dict:
    r = _run(["wg-quick", "up", WG_IFACE])
    if r.returncode != 0:
        return {"error": r.stderr.strip() or r.stdout.strip()}
    return {"ok": True}


def stop_wg() -> dict:
    r = _run(["wg-quick", "down", WG_IFACE])
    if r.returncode != 0:
        return {"error": r.stderr.strip() or r.stdout.strip()}
    return {"ok": True}


def add_peer(name: str) -> dict:
    """Generate client keys, assign IP, update conf, return client conf text."""
    if not WG_CONF.exists():
        return {"error": "Servidor no inicializado. Configura primero."}

    # Generate client keys
    r_priv = _run(["wg", "genkey"])
    if r_priv.returncode != 0:
        return {"error": "No se pudo generar clave privada de cliente"}
    client_priv = r_priv.stdout.strip()

    r_pub = subprocess.run(["wg", "pubkey"], input=client_priv, capture_output=True, text=True, timeout=5)
    if r_pub.returncode != 0:
        return {"error": "No se pudo derivar clave publica de cliente"}
    client_pub = r_pub.stdout.strip()

    # Get server pubkey
    priv_path = Path("/etc/wireguard/server_private.key")
    server_pub = ""
    if priv_path.exists():
        r2 = subprocess.run(["wg", "pubkey"], input=priv_path.read_text().strip(),
                            capture_output=True, text=True, timeout=5)
        if r2.returncode == 0:
            server_pub = r2.stdout.strip()

    # Parse existing conf to find used IPs
    conf_text = WG_CONF.read_text()
    parsed = _parse_wg_conf(conf_text)
    existing_ips = []
    for p in parsed["peers"]:
        ip = p.get("AllowedIPs", "").split("/")[0]
        if ip:
            existing_ips.append(ip)

    try:
        client_ip = _next_peer_ip(existing_ips)
    except RuntimeError as e:
        return {"error": str(e)}

    # Append peer to conf
    new_peer_block = f"\n[Peer]\nPublicKey = {client_pub}\nAllowedIPs = {client_ip}/32\n"
    _atomic_write(WG_CONF, conf_text + new_peer_block)

    # If wg0 is up, hot-add peer
    _run(["wg", "set", WG_IFACE, "peer", client_pub, "allowed-ips", f"{client_ip}/32"])

    # Save name mapping
    names = _read_peers_cfg()
    names[client_pub] = name or f"peer-{client_ip.split('.')[-1]}"
    _write_peers_cfg(names)

    # Build client .conf
    cfg = _read_panel_cfg()
    endpoint = cfg.get("endpoint", "")
    client_conf = f"""[Interface]
PrivateKey = {client_priv}
Address = {client_ip}/24
DNS = 1.1.1.1

[Peer]
PublicKey = {server_pub}
Endpoint = {endpoint}
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
"""
    return {
        "ok":          True,
        "pubkey":      client_pub,
        "ip":          client_ip,
        "client_conf": client_conf,
        "qr_svg":      _make_qr_svg(client_conf),
        "name":        names[client_pub],
    }


def delete_peer(pubkey: str) -> dict:
    if not WG_CONF.exists():
        return {"error": "wg0.conf no existe"}

    conf_text = WG_CONF.read_text()
    parsed = _parse_wg_conf(conf_text)

    new_peers = [p for p in parsed["peers"] if p.get("PublicKey", "") != pubkey]
    if len(new_peers) == len(parsed["peers"]):
        return {"error": "Peer no encontrado"}

    new_conf = _build_wg_conf(parsed["interface"], new_peers)
    _atomic_write(WG_CONF, new_conf)

    # Hot-remove if running
    _run(["wg", "set", WG_IFACE, "peer", pubkey, "remove"])

    # Remove from names
    names = _read_peers_cfg()
    names.pop(pubkey, None)
    _write_peers_cfg(names)

    return {"ok": True}


def rename_peer(pubkey: str, name: str) -> dict:
    names = _read_peers_cfg()
    if pubkey not in names and not name:
        return {"error": "Peer no encontrado"}
    names[pubkey] = name
    _write_peers_cfg(names)
    return {"ok": True}

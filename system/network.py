"""Network information backend for ix2panel."""
import ipaddress
import re
import socket
import subprocess
from pathlib import Path

import psutil


def get_network_info() -> dict:
    addrs  = psutil.net_if_addrs()
    stats  = psutil.net_if_stats()
    io_per = psutil.net_io_counters(pernic=True) or {}

    SKIP = {"lo"}
    ifaces = []

    for name, addr_list in addrs.items():
        if name in SKIP:
            continue

        ipv4 = ""
        mask = ""
        mac  = ""
        ipv6 = ""

        for a in addr_list:
            family = a.family.name if hasattr(a.family, "name") else str(a.family)
            if family == "AF_INET" and not ipv4:
                ipv4 = a.address
                mask = a.netmask or ""
            elif family == "AF_INET6" and not ipv6:
                ipv6 = a.address.split("%")[0]
            elif family in ("AF_LINK", "AF_PACKET") and not mac:
                mac = a.address

        st    = stats.get(name)
        is_up = bool(st and st.isup)
        speed = (st.speed if st else 0) or 0

        io       = io_per.get(name)
        rx_bytes = io.bytes_recv if io else 0
        tx_bytes = io.bytes_sent if io else 0

        ifaces.append({
            "name":     name,
            "up":       is_up,
            "ipv4":     ipv4,
            "mask":     mask,
            "ipv6":     ipv6,
            "mac":      mac,
            "speed":    speed,
            "rx_bytes": rx_bytes,
            "tx_bytes": tx_bytes,
        })

    # Up first, then alphabetical
    ifaces.sort(key=lambda x: (not x["up"], x["name"]))

    # Hostname
    try:
        hostname = socket.gethostname()
    except Exception:
        hostname = ""

    # Default gateway
    gateway = ""
    try:
        r = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True, text=True, timeout=5
        )
        m = re.search(r"default via (\S+)", r.stdout)
        if m:
            gateway = m.group(1)
    except Exception:
        pass

    # DNS servers
    dns = []
    try:
        text = Path("/etc/resolv.conf").read_text()
        for line in text.splitlines():
            m = re.match(r"nameserver\s+(\S+)", line)
            if m:
                dns.append(m.group(1))
    except Exception:
        pass

    return {
        "hostname": hostname,
        "gateway":  gateway,
        "dns":      dns,
        "ifaces":   ifaces,
    }


def get_ddns_status() -> dict:
    """Lee config de ddclient y estado del servicio."""
    import re, subprocess
    result = {
        "installed": False,
        "running": False,
        "hostname": "",
        "login": "",
        "public_ip": "",
        "last_update": "",
        "status": "unknown",
        "status_msg": "",
    }

    # Comprobar si ddclient está instalado
    import shutil
    if not shutil.which("ddclient") and not Path("/usr/sbin/ddclient").exists():
        return result
    result["installed"] = True

    # Estado del servicio
    try:
        r = subprocess.run(["systemctl", "is-active", "ddclient"],
                           capture_output=True, text=True, timeout=5)
        result["running"] = r.stdout.strip() == "active"
    except Exception:
        pass

    # Leer config
    conf_path = Path("/etc/ddclient.conf")
    if conf_path.exists():
        try:
            text = conf_path.read_text()
            m = re.search(r"login\s*=\s*(\S+)", text)
            if m:
                result["login"] = m.group(1).strip("'\"")
            # Hostname: línea que contiene .ddns. o similar (no tiene =)
            for line in text.splitlines():
                line = line.strip().rstrip(",\\").strip()
                if line and not line.startswith("#") and "=" not in line and "." in line:
                    result["hostname"] = line
                    break
        except Exception:
            pass

    # IP pública actual
    try:
        r = subprocess.run(["curl", "-s", "-4", "--max-time", "5", "https://ipinfo.io/ip"],
                           capture_output=True, text=True, timeout=8)
        ip = r.stdout.strip()
        # Solo se muestra si es una IP de verdad (no una pagina de error ni basura)
        if r.returncode == 0 and ip:
            ipaddress.ip_address(ip)
            result["public_ip"] = ip
    except Exception:
        pass

    # Última actualización desde el log de ddclient
    try:
        r = subprocess.run(["journalctl", "-u", "ddclient", "-n", "60", "--no-pager", "--output=short"],
                           capture_output=True, text=True, timeout=5)
        all_lines = r.stdout.splitlines()
        # Quedarse solo con las líneas del último arranque del servicio
        last_start = 0
        for i, line in enumerate(all_lines):
            if "Started ddclient" in line:
                last_start = i
        lines = all_lines[last_start:]

        for line in lines:
            if "nochg" in line or ("good" in line and "ddclient" in line.lower()):
                ts = line.split("debian")[0].strip() if "debian" in line else line[:20]
                result["last_update"] = ts
                result["status"] = "ok"
                result["status_msg"] = "OK — sin cambio de IP"
                break
            elif "badauth" in line or "401" in line:
                result["status"] = "err"
                result["status_msg"] = "Error de autenticación"
                break
            elif "Could not determine" in line:
                result["status"] = "warn"
                result["status_msg"] = "No se pudo determinar la IP"
                break
            elif "skipping update" in line:
                result["status"] = "warn"
                result["status_msg"] = "Esperando reintento (cooldown)"
                break
        if not result["status_msg"]:
            result["status"] = "ok" if result["running"] else "warn"
            result["status_msg"] = "Activo" if result["running"] else "Inactivo"
    except Exception:
        if result["running"]:
            result["status"] = "ok"
            result["status_msg"] = "Activo"

    return result

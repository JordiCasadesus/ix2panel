import os
import platform


def _read_first_line(path: str):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            line = (f.readline() or "").strip()
            return line or None
    except Exception:
        return None


def _read_os_release_pretty():
    try:
        data = {}
        with open("/etc/os-release", "r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                raw = raw.strip()
                if not raw or raw.startswith("#") or "=" not in raw:
                    continue
                k, v = raw.split("=", 1)
                v = v.strip().strip('"')
                data[k.strip()] = v
        return data.get("PRETTY_NAME") or None
    except Exception:
        return None


def get_firmware():
    env = (os.environ.get("IX2_FIRMWARE") or "").strip()
    if env:
        return env

    # Archivos típicos donde algunos sistemas guardan "la versión"
    candidates = [
        "/etc/ix2panel/version",
        "/etc/ix2panel/VERSION",
        "/etc/ix2-ng/version",
        "/etc/ix2-ng/VERSION",
        "/etc/version",
        "/etc/firmware_version",
        "/etc/openmediavault/version",
        "/etc/debian_version",
    ]
    for p in candidates:
        val = _read_first_line(p)
        if val:
            return val

    pretty = _read_os_release_pretty()
    kernel = (platform.release() or "").strip()
    if pretty and kernel and kernel not in pretty:
        return f"{pretty} ({kernel})"
    if pretty:
        return pretty
    if kernel:
        return kernel
    return "—"


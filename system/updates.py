import subprocess
import time
from pathlib import Path
from system.firmware import get_firmware


def _run(cmd, timeout=None):
    p = subprocess.run(
        cmd,
        text=True,
        errors="replace",
        capture_output=True,
        timeout=timeout
    )
    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()
    combined = out
    if err:
        combined = (combined + "\n" + err).strip() if combined else err
    return p.returncode, combined


def get_updates():
    updates = []
    firmware = get_firmware()
    try:
        # apt imprime WARNINGs por stderr; no deben contarse como "paquetes pendientes"
        p = subprocess.run(
            ["apt", "list", "--upgradable"],
            text=True,
            errors="replace",
            capture_output=True
        )
        out = p.stdout or ""
        err = p.stderr or ""
        if p.returncode != 0:
            msg = (err.strip() or out.strip() or "Error ejecutando apt")
            return {"error": msg, "items": updates, "firmware": firmware}
    except Exception as e:
        return {"error": str(e), "items": updates, "firmware": firmware}

    for line in out.splitlines():
        line = (line or "").strip()
        if not line or line.startswith("Listing") or line.startswith("WARNING:"):
            continue
        parts = line.split()
        if not parts:
            continue
        name_ver = parts[0]
        if "/" in name_ver:
            name = name_ver.split("/")[0]
        else:
            name = name_ver
        version = parts[1] if len(parts) > 1 else "—"
        updates.append({
            "name": name,
            "detail": f"→ {version}",
            "version": version,
            "size": "—"
        })

    return {
        "items": updates,
        "count": len(updates),
        "last_check": time.strftime("%Y-%m-%d %H:%M:%S"),
        "firmware": firmware
    }


def run_updates():
    log_path = Path("/var/log/ix2panel-updates.log")

    # Evita lanzamientos concurrentes.
    pid_path = Path("/var/run/ix2panel-updates.pid")
    try:
        if pid_path.exists():
            old_pid = int((pid_path.read_text(encoding="utf-8", errors="replace") or "0").strip() or "0")
            if old_pid > 1:
                try:
                    # kill -0: proceso existe
                    subprocess.check_call(["kill", "-0", str(old_pid)])
                    return {"ok": True, "output": f"Actualizacion ya en curso (PID {old_pid}). Revisa {log_path}."}
                except Exception:
                    pass
    except Exception:
        pass

    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = (
            "set -e; "
            f'echo "=== ix2panel update start: $(date) ===" >> {log_path}; '
            f"sudo -n apt-get update >> {log_path} 2>&1; "
            f"sudo -n apt-get -y upgrade >> {log_path} 2>&1; "
            f'echo "=== ix2panel update end: $(date) ===" >> {log_path}; '
            f"rm -f {pid_path}"
        )
        p = subprocess.Popen(
            ["bash", "-lc", cmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        try:
            pid_path.write_text(str(p.pid), encoding="utf-8")
        except Exception:
            pass

        return {"ok": True, "output": f"Actualizacion iniciada en segundo plano (PID {p.pid}). Log: {log_path}"}
    except Exception as e:
        return {"error": str(e)}


def reboot_system():
    try:
        # Programa reinicio con pequeno retraso para responder al cliente.
        subprocess.Popen(
            ["sudo", "-n", "bash", "-lc", "sleep 2; systemctl reboot"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return {"ok": True, "output": "Reinicio solicitado..."}
    except Exception as e:
        return {"error": str(e)}


def shutdown_system():
    try:
        subprocess.Popen(
            ["sudo", "-n", "bash", "-lc", "sleep 2; systemctl poweroff"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return {"ok": True, "output": "Apagado solicitado..."}
    except Exception as e:
        return {"error": str(e)}


def restart_panel_service():
    try:
        # Reinicia con retardo para poder responder al cliente antes de que caiga el proceso.
        subprocess.Popen(
            ["sudo", "-n", "bash", "-lc", "sleep 2; systemctl restart ix2panel"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return {"ok": True, "output": "Reinicio del panel solicitado..."}
    except Exception as e:
        return {"error": str(e)}


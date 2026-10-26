import ipaddress, subprocess, re

def get_fail2ban_status():
    try:
        r = subprocess.run(["fail2ban-client", "status"], capture_output=True, text=True, timeout=5)
        jails = re.findall(r"Jail list:\s*(.+)", r.stdout)
        jail_list = [j.strip() for j in jails[0].split(",")] if jails else []
        result = {"jails": []}
        for jail in jail_list:
            r2 = subprocess.run(["fail2ban-client", "status", jail], capture_output=True, text=True, timeout=5)
            banned = re.findall(r"Banned IP list:\s*(.*)", r2.stdout)
            total = re.findall(r"Total banned:\s*(\d+)", r2.stdout)
            current = re.findall(r"Currently banned:\s*(\d+)", r2.stdout)
            result["jails"].append({
                "name": jail,
                "currently_banned": int(current[0]) if current else 0,
                "total_banned": int(total[0]) if total else 0,
                "banned_ips": banned[0].strip().split() if banned and banned[0].strip() else []
            })
        return result
    except Exception as e:
        return {"error": str(e), "jails": []}

def unban_ip(jail, ip):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", str(jail)):
        return {"ok": False, "error": "jail invalido"}
    try:
        ipaddress.ip_address(str(ip))
    except ValueError:
        return {"ok": False, "error": "ip invalida"}
    try:
        r = subprocess.run(["fail2ban-client", "set", jail, "unbanip", ip], capture_output=True, text=True, timeout=5)
        return {"ok": r.returncode == 0, "output": r.stdout.strip()}
    except Exception as e:
        return {"ok": False, "error": str(e)}

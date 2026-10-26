import time

ALERT_CACHE = {}  # key -> {"level": str, "first_ts": float, "last_ts": float}
ALERT_TTL_SEC = 300  # mantener alertas recientes 5 min aunque ya no estén activas


def _human_age(ts: float) -> str:
    d = int(time.time() - ts)
    if d < 60:
        return "ahora"
    if d < 3600:
        return f"{d//60} min"
    return f"{d//3600} h"


def _level_weight(level: str) -> int:
    if level == "err":
        return 2
    if level == "warn":
        return 1
    return 0


def get_alerts(sysinfo):
    now = time.time()
    raw_alerts = []

    # ================= CPU =================
    cpu = sysinfo.get("cpu_percent", 0)
    if cpu >= 90:
        raw_alerts.append({"level": "err", "text": "CPU al 90%+"})
    elif cpu >= 80:
        raw_alerts.append({"level": "warn", "text": "CPU alta (>80%)"})

    # ================= DISCOS =================
    for d in sysinfo.get("disks", []):
        # SYS
        for p in d.get("partitions", []):
            if p.get("role") == "SYS":
                used = float(p.get("used") or 0)
                total = float(p.get("total") or 0)
                pct = int((used / max(total, 0.001)) * 100)
                if pct >= 90:
                    raw_alerts.append({"level": "err", "text": "SYS casi lleno (≥90%)"})
                elif pct >= 80:
                    raw_alerts.append({"level": "warn", "text": "SYS alto (≥80%)"})

        # disco datos global
        if d.get("percent", 0) >= 90:
            raw_alerts.append({"level": "err", "text": "Disco datos ≥90%"})
        elif d.get("percent", 0) >= 80:
            raw_alerts.append({"level": "warn", "text": "Disco datos ≥80%"})

    # ================= SERVICIOS =================
    for s in sysinfo.get("services", []):
        if s.get("status") == "err":
            raw_alerts.append({
                "level": "err",
                "text": f"Servicio caído: {s.get('name', 'desconocido')}"
            })

    # ================= BACKUP =================
    backup = sysinfo.get("backup")
    if backup:
        state = backup.get("state")
        if state == "err":
            raw_alerts.append({"level": "err", "text": "Backup no encontrado"})
        elif state == "warn":
            hours = backup.get("age_hours", "?")
            raw_alerts.append({"level": "warn", "text": f"Backup desactualizado ({hours} h)"})

    # ================= PERSISTENCIA (RECENT) =================
    active_map = {a["text"]: a["level"] for a in raw_alerts}

    # Actualiza cache: last_ts siempre que está activa; first_ts sólo al inicio/cambio de nivel
    for key, level in active_map.items():
        prev = ALERT_CACHE.get(key)
        if (not prev) or prev.get("level") != level or (now - prev.get("last_ts", 0)) > ALERT_TTL_SEC:
            ALERT_CACHE[key] = {"level": level, "first_ts": now, "last_ts": now}
        else:
            prev["last_ts"] = now

    # Expira del cache sólo las inactivas que ya pasaron TTL
    for key in list(ALERT_CACHE.keys()):
        if key in active_map:
            continue
        if (now - ALERT_CACHE[key].get("last_ts", 0)) > ALERT_TTL_SEC:
            del ALERT_CACHE[key]

    out = []

    # Activas primero (orden determinista)
    active_items = [(k, ALERT_CACHE.get(k)) for k in active_map.keys()]
    active_items = [(k, e) for (k, e) in active_items if e]
    active_items.sort(key=lambda kv: (_level_weight(kv[1]["level"]), kv[1].get("last_ts", 0)), reverse=True)
    for key, entry in active_items:
        out.append({"level": entry["level"], "text": key, "age": _human_age(entry["first_ts"])})

    # Luego recientes inactivas (TTL) (orden determinista)
    inactive_items = [(k, v) for (k, v) in ALERT_CACHE.items() if k not in active_map]
    inactive_items.sort(key=lambda kv: (_level_weight(kv[1]["level"]), kv[1].get("last_ts", 0)), reverse=True)
    for key, entry in inactive_items:
        out.append({"level": entry["level"], "text": key, "age": _human_age(entry["first_ts"])})

    if not out:
        out = [{"level": "ok", "text": "Sistema estable", "age": ""}]

    return out[:5]

import json
import subprocess
from datetime import datetime
from system.devices import annotate_text


def _format_ts(us_str):
    try:
        us = int(us_str)
        return datetime.fromtimestamp(us / 1_000_000).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "-"


def _ts_us(us_str):
    try:
        return int(us_str)
    except Exception:
        return 0


def _entry_type(entry):
    try:
        prio = int(entry.get("PRIORITY", "9"))
    except Exception:
        prio = 9

    if prio <= 3:
        return "error"

    if entry.get("_SYSTEMD_UNIT") or entry.get("SYSLOG_IDENTIFIER"):
        return "service"

    return "all"


def get_logs(filter_type="all", query="", limit=200):
    # In "all" we read a wider window so errors are not hidden by noisy services.
    limit = int(limit)
    effective_limit = limit
    if filter_type == "all":
        effective_limit = max(limit * 5, limit)

    cmd = ["journalctl", "--no-pager", "-n", str(effective_limit), "-o", "json"]
    if filter_type == "error":
        cmd.extend(["-p", "err"])

    try:
        out = subprocess.check_output(cmd, text=True, errors="replace")
    except Exception as e:
        return {"error": str(e), "items": []}

    items = []
    q = (query or "").lower()

    for line in out.splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except Exception:
            continue

        msg = entry.get("MESSAGE", "")
        unit = entry.get("_SYSTEMD_UNIT") or entry.get("SYSLOG_IDENTIFIER") or entry.get("_COMM") or "system"
        ts_raw = entry.get("__REALTIME_TIMESTAMP")
        ts = _format_ts(ts_raw)
        ts_us = _ts_us(ts_raw)
        typ = _entry_type(entry)

        if filter_type != "all" and typ != filter_type:
            continue

        if q and q not in msg.lower() and q not in unit.lower():
            continue

        msg = annotate_text(msg)  # solo para mostrar: la busqueda ya se hizo sobre el texto original
        title = msg if msg else unit
        detail = f"{unit} · {ts}"
        items.append(
            {
                "type": typ,
                "title": title[:80],
                "detail": detail,
                "source": unit,
                "date": ts,
                "message": msg[:500],
                "ts_us": ts_us,
            }
        )

    # Always sort by datetime descending (newest first).
    items.sort(key=lambda x: int(x.get("ts_us", 0)), reverse=True)
    items = items[:limit]
    for it in items:
        it.pop("ts_us", None)

    return {"items": items}

from pathlib import Path
from datetime import datetime, timezone

MAX_AGE_HOURS = 24

def check_backup(paths):
    now = datetime.now(timezone.utc)

    newest = None
    found = False

    for p in paths:
        path = Path(p)

        if not path.exists() or not path.is_dir():
            continue

        files = [f for f in path.rglob("*") if f.is_file()]
        if not files:
            continue

        found = True

        for f in files:
            mtime = datetime.fromtimestamp(
                f.stat().st_mtime, tz=timezone.utc
            )
            if not newest or mtime > newest:
                newest = mtime

    if not found:
        return {
            "state": "err",
            "reason": "not_found"
        }

    age_hours = int((now - newest).total_seconds() / 3600)

    if age_hours > MAX_AGE_HOURS:
        return {
            "state": "warn",
            "age_hours": age_hours
        }

    return {
        "state": "ok",
        "age_hours": age_hours
    }

# eventlet.monkey_patch() DEBE ser lo primero que se ejecuta en todo el proceso,
# antes de cualquier otro import (incluido Flask): parchea threading/socket/ssl
# a nivel de modulo, y cualquier libreria importada antes se queda con las
# clases originales sin parchear. Es lo que necesita el WebSocket de la
# Terminal (flask-sock/simple-websocket) para funcionar bajo el worker
# "eventlet" de Gunicorn sin corromper la conexion.
try:
    import eventlet
    eventlet.monkey_patch()
except Exception:
    pass

from flask import Flask, render_template, jsonify, request, session, redirect
from functools import wraps
import json
import os
import re
import secrets
import threading
import platform
from datetime import datetime
import ipaddress
import psutil
import time
import subprocess
from werkzeug.security import generate_password_hash, check_password_hash

try:
    import paramiko  # type: ignore
except Exception:
    paramiko = None

try:
    from flask_sock import Sock  # type: ignore
except Exception:
    Sock = None

# from system.__init__ import 
from system.disks import get_disks, get_disks_tree_all, mount_partition, unmount_partition  
from system.services import get_services_status, get_service_detail, restart_service, stop_service, enable_firewall, disable_firewall
from system.alerts import get_alerts
from system.users import get_users, create_user, update_user, delete_user
from system.logs import get_logs
from system.devices import device_in_text
from system.updates import get_updates, run_updates, reboot_system, restart_panel_service, shutdown_system
from system.kernel import get_status as kernel_status, get_job as kernel_job, save_upload as kernel_save_upload, start_prepare as kernel_start_prepare, activate as kernel_activate, discard as kernel_discard
from system.power import get_power_status, set_schedule
from system.backups import get_backups, get_backup_progress, get_backup_log, browse_pc_dirs, get_ssh_pubkey, generate_ssh_key, start_scheduler, get_running_job_names, mask_secrets
from system.groups import get_groups, create_group, add_user_to_group, remove_user_from_group
from system.fs import list_dir, create_dir, remove_dir
from system.mounts import list_mounts, mount_smb, unmount
from system.shares import list_shares, create_share, update_share, delete_share, reload_samba
import system.wireguard as wg_mod
from system.network import get_network_info
from system.wol import send_wol, get_wol_targets, add_wol_target, edit_wol_target, delete_wol_target, shutdown_target as wol_shutdown
from system.fail2ban import get_fail2ban_status, unban_ip as fail2ban_unban_ip

app = Flask(__name__)
sock = Sock(app) if Sock else None

# Sin ping_interval, simple_websocket no manda pings ni detecta nunca una
# conexion muerta por su cuenta: si el navegador desaparece sin cerrar limpio
# el WebSocket (portatil que se suspende, wifi que cae, pestana matada a la
# fuerza...), tanto el hilo interno de simple_websocket como ws.receive() en
# ws_ssh() se quedan bloqueados para siempre (Gunicorn corre con --timeout 0
# a proposito, asi que nadie los mata). Cada terminal huerfana asi deja vivos
# 2 hilos + una conexion SSH real (con su sshd-session en el NAS) para
# siempre. Con ping_interval, simple_websocket detecta el pong ausente y
# cierra la conexion sola (ws.py: Base._thread), lo que desbloquea
# ws.receive() y deja que el finally de ws_ssh() limpie chan/client/hilos.
if sock:
    app.config["SOCK_SERVER_OPTIONS"] = {"ping_interval": 25}

# PID de este proceso: sirve para que el overlay de "Reiniciar panel" del
# frontend distinga un 200 OK del proceso VIEJO (que sigue vivo un instante
# tras el click, mientras "sleep 2; systemctl restart" arranca en segundo
# plano) de uno del proceso NUEVO ya listo. Sin esto, el primer poll del
# JS casi siempre acierta contra el viejo, la barra da por bueno el reinicio
# antes de tiempo y el reload cae justo en la ventana en que el viejo ya
# murio y el nuevo aun no escucha -> 502.
_PANEL_PID = os.getpid()

_PROC_CPU_CACHE = {}
_CRED_CACHE = {}  # sid -> {"username": str, "password": str, "ts": float}
_CRED_TTL_SEC = 15 * 60
_LOGIN_FAIL = {}  # ip -> {"count": int, "ts": float}
_LOGIN_LOCK_SEC = 60
_PANEL_AUTH_FILE = os.environ.get("IX2PANEL_PANEL_AUTH_FILE") or os.path.join(os.path.dirname(__file__), ".panel_auth.json")
_PANEL_AUTH_MODE = "panel"
_PANEL_AUTH_USER = ""
_PANEL_AUTH_PASS = ""
_PANEL_AUTH_SRC = "unknown"  # configured|file|generated|unknown
_PANEL_AUTH_USERS = []  # list of {"user": str, "pass_hash": str, "role": str}
_ROLE_ORDER = {"viewer": 0, "operator": 1, "admin": 2}
_PANEL_USER_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
_PANEL_USERS_LOCK = threading.Lock()

def _atomic_write_text(path: str, text: str):
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _is_hash_value(value: str) -> bool:
    v = (value or "").strip().lower()
    return v.startswith("pbkdf2:") or v.startswith("scrypt:")


def _hash_password(password: str) -> str:
    return generate_password_hash(password)


def _build_user_record(user: str, password: str, role: str = "admin"):
    user = (user or "").strip()
    password = password or ""
    role = (role or "admin").strip().lower()
    if not user or not password:
        return None
    if _is_hash_value(password):
        pass_hash = password
    else:
        pass_hash = _hash_password(password or "")
    return {"user": user, "pass_hash": pass_hash, "role": role}


def _verify_user_password(user_obj: dict, password: str) -> bool:
    if not isinstance(user_obj, dict):
        return False
    pass_hash = (user_obj.get("pass_hash") or "").strip()
    if pass_hash:
        try:
            return bool(check_password_hash(pass_hash, password or ""))
        except Exception:
            return False
    # Compatibilidad con ficheros legacy en texto plano.
    legacy = user_obj.get("pass") or ""
    return bool(legacy and legacy == (password or ""))


def _normalize_users(users):
    out = []
    for item in (users or []):
        if not isinstance(item, dict):
            continue
        rec = _build_user_record(
            item.get("user"),
            item.get("pass_hash") or item.get("pass") or "",
            item.get("role") or "viewer",
        )
        if rec:
            out.append(rec)
    return out


def _persist_panel_users(users):
    clean = _normalize_users(users)
    _atomic_write_text(_PANEL_AUTH_FILE, json.dumps({"users": clean}, indent=2) + "\n")
    try:
        if os.name != "nt":
            os.chmod(_PANEL_AUTH_FILE, 0o600)
    except Exception:
        pass


def _salvage_panel_auth_from_text(raw: str):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            if isinstance(data.get("users"), list):
                users = []
                for item in data.get("users"):
                    if not isinstance(item, dict):
                        continue
                    u = (item.get("user") or "").strip()
                    p = item.get("pass_hash") or item.get("pass") or ""
                    r = (item.get("role") or "viewer").strip().lower()
                    if u and p:
                        rec = _build_user_record(u, p, r)
                        if rec:
                            users.append(rec)
                if users:
                    return {"users": users}
            u = (data.get("user") or "").strip()
            p = data.get("pass_hash") or data.get("pass") or ""
            r = (data.get("role") or "admin").strip().lower()
            if u and p:
                rec = _build_user_record(u, p, r)
                if rec:
                    return {"users": [rec]}
    except Exception:
        pass

    # Recupera el ultimo objeto JSON si el fichero esta "pegado" (p.ej. {...}{...}{...})
    objs = []
    depth = 0
    start = None
    for i, ch in enumerate(raw):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    objs.append(raw[start : i + 1])
                    start = None

    for obj in reversed(objs):
        try:
            data = json.loads(obj)
            if isinstance(data, dict):
                u = (data.get("user") or "").strip()
                p = data.get("pass_hash") or data.get("pass") or ""
                if u and p:
                    rec = _build_user_record(u, p, "admin")
                    if rec:
                        return {"users": [rec]}
        except Exception:
            continue

    return None

@app.context_processor
def _inject_static_ver():
    def static_ver(rel_path: str) -> int:
        try:
            full = os.path.join(app.root_path, "static", rel_path)
            return int(os.path.getmtime(full))
        except Exception:
            return 0

    panel_can_change = bool(_PANEL_AUTH_MODE == "panel" and _PANEL_AUTH_SRC != "configured")
    panel_locked_reason = ""
    if _PANEL_AUTH_MODE != "panel":
        panel_locked_reason = "El panel esta en modo AUTH_MODE distinto de 'panel'."
    elif _PANEL_AUTH_SRC == "configured":
        panel_locked_reason = "Credenciales fijadas por config.py o variables de entorno."

    return {
        "static_ver": static_ver,
        "ssh_terminal_enabled": bool(paramiko and sock),
        "panel_auth_mode": _PANEL_AUTH_MODE,
        "panel_auth_user": _PANEL_AUTH_USER,
        "panel_auth_can_change": panel_can_change,
        "panel_auth_locked_reason": panel_locked_reason,
        "panel_role": session.get("role") or "viewer",
    }


def _load_or_create_secret_key():
    env = (os.environ.get("IX2PANEL_SECRET_KEY") or "").strip()
    if env:
        return env

    secret_file = (os.environ.get("IX2PANEL_SECRET_FILE") or "").strip()
    if not secret_file:
        secret_file = os.path.join(os.path.dirname(__file__), ".secret_key")

    try:
        if os.path.exists(secret_file):
            try:
                os.chmod(secret_file, 0o600)  # la clave que firma las sesiones no debe ser legible por otros usuarios
            except Exception:
                pass
            with open(secret_file, "r", encoding="utf-8", errors="replace") as f:
                val = (f.read() or "").strip()
                if val:
                    return val
    except Exception:
        pass

    val = secrets.token_urlsafe(48)
    try:
        fd = os.open(secret_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(val)
    except Exception:
        pass

    return val


app.secret_key = _load_or_create_secret_key()
cookie_secure = (os.environ.get("IX2PANEL_SESSION_COOKIE_SECURE", "1") or "1").strip().lower() not in ("0", "false", "no", "off")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    SESSION_COOKIE_SECURE=cookie_secure,
)
_BUILD_ID = str(int(os.path.getmtime(__file__))) if os.path.exists(__file__) else "0"


def _load_or_create_panel_auth():
    env_user = (os.environ.get("IX2PANEL_PANEL_USER") or "").strip()
    env_pass = os.environ.get("IX2PANEL_PANEL_PASS") or ""

    cfg_user = ""
    cfg_pass = ""
    cfg_mode = ""
    try:
        import config as _cfg  # type: ignore
        cfg_user = (getattr(_cfg, "PANEL_USER", "") or getattr(_cfg, "PANEL_USERNAME", "") or "").strip()
        cfg_pass = getattr(_cfg, "PANEL_PASS", "") or getattr(_cfg, "PANEL_PASSWORD", "") or ""
        cfg_mode = (getattr(_cfg, "AUTH_MODE", "") or "").strip().lower()
    except Exception:
        pass

    auth_mode = (os.environ.get("IX2PANEL_AUTH_MODE") or cfg_mode or "panel").strip().lower()
    panel_user = env_user or cfg_user
    panel_pass = env_pass or cfg_pass

    if auth_mode != "panel":
        return auth_mode, [], "configured"

    if panel_user and panel_pass:
        rec = _build_user_record(panel_user, panel_pass, "admin")
        return auth_mode, [rec] if rec else [], "configured"

    # Auto-genera credenciales para evitar quedarte bloqueado si no se configuro nada.
    try:
        if os.path.exists(_PANEL_AUTH_FILE):
            with open(_PANEL_AUTH_FILE, "r", encoding="utf-8", errors="replace") as f:
                raw = f.read() or ""
                data = _salvage_panel_auth_from_text(raw)
                if data:
                    # Si estaba corrupto, lo reescribimos limpio.
                    try:
                        _persist_panel_users(data.get("users") or [])
                    except Exception:
                        pass
                    users = data.get("users") or []
                    if users:
                        return auth_mode, _normalize_users(users), "file"
    except Exception:
        pass

    gen_user = "admin"
    gen_pass = secrets.token_urlsafe(12)
    try:
        rec = _build_user_record(gen_user, gen_pass, "admin")
        _persist_panel_users([rec] if rec else [])
    except Exception:
        pass

    print(f"[ix2panel] Credenciales generadas en {_PANEL_AUTH_FILE} (usuario: {gen_user})", flush=True)
    rec = _build_user_record(gen_user, gen_pass, "admin")
    return auth_mode, [rec] if rec else [], "generated"


def _init_panel_auth():
    global _PANEL_AUTH_MODE, _PANEL_AUTH_USER, _PANEL_AUTH_PASS, _PANEL_AUTH_SRC, _PANEL_AUTH_USERS
    mode, users, src = _load_or_create_panel_auth()
    _PANEL_AUTH_MODE = mode
    _PANEL_AUTH_USERS = users or []
    # Hint (first admin) for login page
    hint = None
    for u in _PANEL_AUTH_USERS:
        if (u.get("role") or "").lower() == "admin":
            hint = u
            break
    if not hint and _PANEL_AUTH_USERS:
        hint = _PANEL_AUTH_USERS[0]
    _PANEL_AUTH_USER = (hint or {}).get("user", "")
    _PANEL_AUTH_PASS = ""
    _PANEL_AUTH_SRC = src

    if mode == "panel":
        if src in ("generated", "file"):
            print(f"[ix2panel] Panel auth en {_PANEL_AUTH_FILE} (usuario: {_PANEL_AUTH_USER})", flush=True)
        else:
            print(f"[ix2panel] Panel auth configurado (usuario: {_PANEL_AUTH_USER})", flush=True)
    else:
        print(f"[ix2panel] Auth mode: {mode}", flush=True)


_init_panel_auth()
start_scheduler()
try:
    from system.notifications import send as _notify_start, start_monitor as _start_monitor
    _notify_start("🟢 <b>ix2Panel NAS arrancado</b>", event="nas_start")
    _start_monitor()
except Exception:
    pass
try:
    from system.smart import start_scheduler as _start_smart_scheduler
    _start_smart_scheduler()
except Exception:
    pass


@app.after_request
def _add_build_header(resp):
    try:
        resp.headers["X-Ix2panel-Build"] = _BUILD_ID
    except Exception:
        pass
    return resp


def _client_ip():
    remote = (request.remote_addr or "").strip()
    use_forwarded = remote in ("127.0.0.1", "::1")
    if use_forwarded:
        # Detras de nginx (127.0.0.1): X-Real-IP lo fija nginx con $remote_addr y el
        # cliente no puede falsificarlo. NO usar el PRIMER valor de X-Forwarded-For:
        # ese lo envia el cliente (nginx anade la IP real AL FINAL), y permitiria
        # saltarse el bloqueo de login rotando IPs falsas.
        xri = (request.headers.get("X-Real-IP") or "").strip()
        xff = (request.headers.get("X-Forwarded-For") or "").strip()
        xff_last = xff.split(",")[-1].strip() if xff else ""
        for cand in (xri, xff_last):
            if not cand:
                continue
            try:
                return str(ipaddress.ip_address(cand))
            except Exception:
                continue
    try:
        return str(ipaddress.ip_address(remote))
    except Exception:
        return remote or "0.0.0.0"


def _cred_prune(now=None):
    now = now or time.time()
    for sid in list(_CRED_CACHE.keys()):
        if (now - _CRED_CACHE[sid].get("ts", 0)) > _CRED_TTL_SEC:
            _CRED_CACHE.pop(sid, None)


def _get_sid():
    sid = session.get("sid")
    if not sid:
        sid = secrets.token_urlsafe(18)
        session["sid"] = sid
    return sid


def _set_creds(username: str, password: str):
    sid = _get_sid()
    _CRED_CACHE[sid] = {"username": username, "password": password, "ts": time.time()}


def _get_creds():
    _cred_prune()
    sid = session.get("sid")
    if not sid:
        return None
    c = _CRED_CACHE.get(sid)
    if not c:
        return None
    if (time.time() - c.get("ts", 0)) > _CRED_TTL_SEC:
        _CRED_CACHE.pop(sid, None)
        return None
    c["ts"] = time.time()
    return c


def _ssh_check_local(username: str, password: str):
    if not paramiko:
        return False, "paramiko no disponible"
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=os.environ.get("IX2PANEL_SSH_HOST", "127.0.0.1"),
            port=int(os.environ.get("IX2PANEL_SSH_PORT", "22")),
            username=username,
            password=password,
            # Hardware ARM antiguo: el hash PAM de la contrasena puede tardar
            # varios segundos, 5s se quedaba corto y disparaba "Authentication timeout".
            timeout=8,
            auth_timeout=20,
            banner_timeout=20,
            allow_agent=False,
            look_for_keys=False,
        )
        return True, None
    except Exception as e:
        return False, str(e)
    finally:
        try:
            client.close()
        except Exception:
            pass


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "No autorizado"}), 401
            return redirect("/login")
        return fn(*args, **kwargs)

    return wrapper


def require_role(min_role: str):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            role = (session.get("role") or "viewer").lower()
            if _ROLE_ORDER.get(role, 0) < _ROLE_ORDER.get(min_role, 0):
                if request.path.startswith("/api/"):
                    return jsonify({"error": "No autorizado"}), 403
                return redirect("/")
            return fn(*args, **kwargs)
        return wrapper
    return deco


@app.before_request
def _auth_guard():
    path = request.path or ""
    if path.startswith("/static/"):
        return None
    if path in ("/login", "/logout"):
        return None
    if path.startswith("/ws/"):
        return None
    if session.get("logged_in"):
        if _PANEL_AUTH_MODE == "panel":
            cur = next((u for u in _PANEL_AUTH_USERS if u.get("user") == session.get("username")), None)
            if cur is None:
                session.clear()
                if path.startswith("/api/"):
                    return jsonify({"error": "No autorizado"}), 401
                return redirect("/login")
            cur_role = (cur.get("role") or "viewer").lower()
            if session.get("role") != cur_role:
                session["role"] = cur_role
        return None
    if path.startswith("/api/"):
        return jsonify({"error": "No autorizado"}), 401
    return redirect("/login")


def _ssh_role(username: str) -> str:
    """Modo 'ssh': admin solo si la cuenta es root o pertenece a sudo/admin/wheel;
    cualquier otra cuenta Linux entra como 'viewer'."""
    try:
        import grp, pwd
        if username == "root":
            return "admin"
        try:
            pgid = pwd.getpwnam(username).pw_gid
        except KeyError:
            return "viewer"
        for g in ("sudo", "admin", "wheel"):
            try:
                ent = grp.getgrnam(g)
            except KeyError:
                continue
            if username in ent.gr_mem or pgid == ent.gr_gid:
                return "admin"
        return "viewer"
    except Exception:
        return "viewer"


@app.route("/login", methods=["GET", "POST"])
def login():
    global _PANEL_AUTH_SRC
    if request.method == "GET":
        return render_template("login.html", error=None, auth_user=_PANEL_AUTH_USER, auth_mode=_PANEL_AUTH_MODE)

    ip = _client_ip()
    now = time.time()
    fail = _LOGIN_FAIL.get(ip)
    if fail and fail.get("count", 0) >= 5 and (now - fail.get("ts", 0)) < _LOGIN_LOCK_SEC:
        return render_template("login.html", error="Demasiados intentos. Espera 1 minuto.", auth_user=_PANEL_AUTH_USER, auth_mode=_PANEL_AUTH_MODE)

    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    if not username or not password:
        return render_template("login.html", error="Usuario y contrasena requeridos.", auth_user=_PANEL_AUTH_USER, auth_mode=_PANEL_AUTH_MODE)

    ok = False
    role = "viewer"
    legacy_upgraded = False
    if _PANEL_AUTH_MODE == "ssh":
        ok, err = _ssh_check_local(username, password)
        if not ok and err and "paramiko no disponible" in err:
            return render_template("login.html", error="Auth SSH no disponible en este NAS (paramiko no instalado).", auth_user=_PANEL_AUTH_USER, auth_mode=_PANEL_AUTH_MODE)
    else:
        for u in _PANEL_AUTH_USERS:
            if username == u.get("user") and _verify_user_password(u, password):
                ok = True
                role = (u.get("role") or "viewer").strip().lower()
                if (not u.get("pass_hash")) and u.get("pass"):
                    u["pass_hash"] = _hash_password(password)
                    u.pop("pass", None)
                    legacy_upgraded = True
                break

    if not ok:
        fail_count = (fail.get("count", 0) + 1) if fail else 1
        _LOGIN_FAIL[ip] = {"count": fail_count, "ts": now}
        if fail_count >= 3:
            try:
                from system.notifications import send as _notify_login
                _notify_login("⚠️ <b>Intentos de login fallidos</b>\nIP: <code>" + ip + "</code>  Intentos: <b>" + str(fail_count) + "</b>", event="login_fail")
            except Exception:
                pass
        return render_template("login.html", error="Credenciales incorrectas.", auth_user=_PANEL_AUTH_USER, auth_mode=_PANEL_AUTH_MODE)

    _LOGIN_FAIL.pop(ip, None)
    if legacy_upgraded and _PANEL_AUTH_MODE == "panel" and _PANEL_AUTH_SRC != "configured":
        try:
            _persist_panel_users(_PANEL_AUTH_USERS)
            _PANEL_AUTH_SRC = "file"
        except Exception:
            pass
    session["logged_in"] = True
    session["username"] = username
    session["role"] = role if _PANEL_AUTH_MODE == "panel" else _ssh_role(username)
    if _PANEL_AUTH_MODE == "ssh":
        # Solo en modo "ssh" el login del panel usa una cuenta Linux real -
        # ahi si tiene sentido reutilizar esas credenciales para la Terminal
        # sin pedirlas otra vez. En modo "panel" (cuenta propia del panel,
        # sin relacion con Linux) NO deben cachearse aqui: la Terminal debe
        # pedir siempre el usuario/contrasena Linux real via su propio
        # formulario (ver ws_ssh) en vez de intentar SSH con credenciales
        # del panel que casi seguro no son una cuenta Linux valida.
        _set_creds(username, password)
    try:
        from system.notifications import send as _notify_login
        _notify_login("🔑 <b>Login en el panel</b>\nUsuario: <b>" + username + "</b>  IP: <code>" + ip + "</code>", event="login_ok")
    except Exception:
        pass
    return redirect("/")


@app.route("/logout")
def logout():
    sid = session.get("sid")
    if sid:
        _CRED_CACHE.pop(sid, None)
    session.clear()
    return redirect("/login")


@app.route("/api/auth/me")
@login_required
def api_auth_me():
    creds = _get_creds()
    return jsonify({
        "username": session.get("username"),
        "has_creds": bool(creds),
        "role": session.get("role") or "viewer",
    })


@app.route("/api/auth/update", methods=["POST"])
@login_required
def api_auth_update():
    global _PANEL_AUTH_USER, _PANEL_AUTH_PASS, _PANEL_AUTH_SRC, _PANEL_AUTH_USERS
    if _PANEL_AUTH_MODE != "panel":
        return jsonify({"error": "No disponible: AUTH_MODE no es 'panel'."}), 400
    if _PANEL_AUTH_SRC == "configured":
        return jsonify({"error": "Credenciales bloqueadas por config.py o variables de entorno."}), 409

    data = request.get_json(silent=True) or {}
    current_password = data.get("current_password") or ""
    new_username = (data.get("new_username") or "").strip()
    new_password = data.get("new_password") or ""

    if not current_password:
        return jsonify({"error": "La contrasena actual es obligatoria."}), 400
    current_user = session.get("username")
    current_user_obj = None
    for u in _PANEL_AUTH_USERS:
        if u.get("user") == current_user:
            current_user_obj = u
            break
    if not _verify_user_password(current_user_obj or {}, current_password):
        return jsonify({"error": "Contrasena actual incorrecta."}), 400

    current_role = (session.get("role") or "viewer").lower()
    if current_role != "admin":
        if new_username and new_username != current_user:
            return jsonify({"error": "No puedes cambiar el usuario."}), 403
        new_username = current_user
    if not new_username:
        new_username = current_user or _PANEL_AUTH_USER
    if any(u is not current_user_obj and (u.get("user") or "").lower() == new_username.lower() for u in _PANEL_AUTH_USERS):
        return jsonify({"error": "Ese usuario ya existe."}), 409
    if not new_password:
        return jsonify({"error": "La nueva contrasena es obligatoria."}), 400

    if len(new_username) > 64:
        return jsonify({"error": "Usuario demasiado largo (max 64)."}), 400
    if len(new_password) < 10:
        return jsonify({"error": "Contrasena demasiado corta (min 10)."}), 400
    has_lower = any(c.islower() for c in new_password)
    has_upper = any(c.isupper() for c in new_password)
    has_digit = any(c.isdigit() for c in new_password)
    if not (has_lower and has_upper and has_digit):
        return jsonify({"error": "La contrasena debe incluir mayusculas, minusculas y numeros."}), 400

    try:
        updated = False
        users = _PANEL_AUTH_USERS[:]
        for u in users:
            if u.get("user") == current_user:
                u["user"] = new_username
                u["pass_hash"] = _hash_password(new_password)
                u.pop("pass", None)
                updated = True
                break
        if not updated and users:
            users[0]["user"] = new_username
            users[0]["pass_hash"] = _hash_password(new_password)
            users[0].pop("pass", None)
        if not users:
            rec = _build_user_record(new_username, new_password, "admin")
            users = [rec] if rec else []
        _persist_panel_users(users)
    except Exception as e:
        return jsonify({"error": f"No se pudo guardar: {e}"}), 500

    _PANEL_AUTH_USERS = users
    _PANEL_AUTH_USER = new_username
    _PANEL_AUTH_PASS = ""
    _PANEL_AUTH_SRC = "file"

    session["username"] = new_username
    return jsonify({"ok": True, "logout": True})


def _panel_users_blocked():
    """None si se pueden editar los usuarios del panel; si no, (mensaje, codigo)."""
    if _PANEL_AUTH_MODE != "panel":
        return "No disponible: AUTH_MODE no es 'panel'.", 400
    if _PANEL_AUTH_SRC == "configured":
        return "Credenciales bloqueadas por config.py o variables de entorno.", 409
    return None


def _password_policy_error(pw: str):
    if len(pw) < 10:
        return "Contrasena demasiado corta (min 10)."
    if len(pw) > 128:
        return "Contrasena demasiado larga (max 128)."
    if not (any(c.islower() for c in pw) and any(c.isupper() for c in pw) and any(c.isdigit() for c in pw)):
        return "La contrasena debe incluir mayusculas, minusculas y numeros."
    return None


def _save_panel_users(users):
    global _PANEL_AUTH_USERS, _PANEL_AUTH_SRC
    _persist_panel_users(users)
    _PANEL_AUTH_USERS = _normalize_users(users)
    _PANEL_AUTH_SRC = "file"


def _admin_count(users) -> int:
    return sum(1 for u in users if (u.get("role") or "").lower() == "admin")


@app.route("/api/panel-users")
@login_required
@require_role("admin")
def api_panel_users_list():
    return jsonify({
        "users": [{"user": u.get("user"), "role": u.get("role")} for u in _PANEL_AUTH_USERS],
        "me": session.get("username"),
    })


@app.route("/api/panel-users", methods=["POST"])
@login_required
@require_role("admin")
def api_panel_users_create():
    blocked = _panel_users_blocked()
    if blocked:
        return jsonify({"error": blocked[0]}), blocked[1]
    data = request.get_json(silent=True) or {}
    name = (data.get("user") or "").strip()
    role = (data.get("role") or "").strip().lower()
    pw = data.get("password") or ""
    if not _PANEL_USER_RE.match(name):
        return jsonify({"error": "Usuario no valido (1-32 caracteres: letras, numeros, . _ -)."}), 400
    if role not in _ROLE_ORDER:
        return jsonify({"error": "Rol no valido."}), 400
    perr = _password_policy_error(pw)
    if perr:
        return jsonify({"error": perr}), 400
    with _PANEL_USERS_LOCK:
        if any((u.get("user") or "").lower() == name.lower() for u in _PANEL_AUTH_USERS):
            return jsonify({"error": "Ese usuario ya existe."}), 409
        rec = {"user": name, "pass_hash": _hash_password(pw), "role": role}
        try:
            _save_panel_users(list(_PANEL_AUTH_USERS) + [rec])
        except Exception as e:
            return jsonify({"error": f"No se pudo guardar: {e}"}), 500
    return jsonify({"ok": True})


@app.route("/api/panel-users/<name>/role", methods=["POST"])
@login_required
@require_role("admin")
def api_panel_users_role(name):
    blocked = _panel_users_blocked()
    if blocked:
        return jsonify({"error": blocked[0]}), blocked[1]
    if not _PANEL_USER_RE.match(name or ""):
        return jsonify({"error": "Usuario no valido."}), 400
    role = ((request.get_json(silent=True) or {}).get("role") or "").strip().lower()
    if role not in _ROLE_ORDER:
        return jsonify({"error": "Rol no valido."}), 400
    if name == session.get("username"):
        return jsonify({"error": "No puedes cambiar tu propio rol."}), 409
    with _PANEL_USERS_LOCK:
        users = [dict(u) for u in _PANEL_AUTH_USERS]
        target = next((u for u in users if u.get("user") == name), None)
        if not target:
            return jsonify({"error": "Usuario no encontrado."}), 404
        if (target.get("role") or "").lower() == "admin" and role != "admin" and _admin_count(users) <= 1:
            return jsonify({"error": "No se puede quitar el ultimo admin."}), 409
        target["role"] = role
        try:
            _save_panel_users(users)
        except Exception as e:
            return jsonify({"error": f"No se pudo guardar: {e}"}), 500
    return jsonify({"ok": True})


@app.route("/api/panel-users/<name>/delete", methods=["POST"])
@login_required
@require_role("admin")
def api_panel_users_delete(name):
    blocked = _panel_users_blocked()
    if blocked:
        return jsonify({"error": blocked[0]}), blocked[1]
    if not _PANEL_USER_RE.match(name or ""):
        return jsonify({"error": "Usuario no valido."}), 400
    if name == session.get("username"):
        return jsonify({"error": "No puedes borrarte a ti mismo."}), 409
    with _PANEL_USERS_LOCK:
        users = [dict(u) for u in _PANEL_AUTH_USERS]
        target = next((u for u in users if u.get("user") == name), None)
        if not target:
            return jsonify({"error": "Usuario no encontrado."}), 404
        if (target.get("role") or "").lower() == "admin" and _admin_count(users) <= 1:
            return jsonify({"error": "No se puede borrar el ultimo admin."}), 409
        users = [u for u in users if u.get("user") != name]
        try:
            _save_panel_users(users)
        except Exception as e:
            return jsonify({"error": f"No se pudo guardar: {e}"}), 500
    return jsonify({"ok": True})


def _parse_ps_table(out: str, cpu_count: int):
    items = []
    for line in out.splitlines():
        line = (line or "").strip()
        if not line:
            continue

        parts = line.split(None, 2)
        if len(parts) < 3:
            continue

        name, pcpu, rss_kb = parts[0], parts[1], parts[2]

        try:
            cpu_raw = float(str(pcpu).replace(",", "."))
        except Exception:
            cpu_raw = 0.0

        # Normaliza a 0..100 (ps puede superar 100 en multi-core)
        try:
            cpu = cpu_raw / max(1, cpu_count)
        except Exception:
            cpu = 0.0
        if cpu < 0:
            cpu = 0.0
        if cpu > 100.0:
            cpu = 100.0

        try:
            ram = float(str(rss_kb).replace(",", ".")) / 1024.0
        except Exception:
            ram = 0.0

        items.append({"name": name or "unknown", "cpu": round(cpu, 1), "ram": round(ram, 1)})

    return items


def _get_top_processes_from_ps(limit=4):
    cpu_count = psutil.cpu_count(logical=True) or 1

    def run_ps(sort_key: str):
        return subprocess.check_output(
            ["ps", "-eo", "comm=,pcpu=,rss=", f"--sort=-{sort_key}"],
            text=True,
            errors="replace"
        )

    try:
        by_cpu = _parse_ps_table(run_ps("pcpu"), cpu_count)
    except Exception:
        by_cpu = []

    selected = []
    seen = set()
    for it in by_cpu:
        name = it.get("name") or ""
        key = (name, it.get("ram", 0.0))
        if key in seen:
            continue
        seen.add(key)
        selected.append(it)
        if len(selected) >= limit:
            return selected[:limit]

    # Si no hay suficientes por CPU, completa por RAM.
    try:
        by_ram = _parse_ps_table(run_ps("rss"), cpu_count)
    except Exception:
        by_ram = []

    for it in by_ram:
        name = it.get("name") or ""
        key = (name, it.get("ram", 0.0))
        if key in seen:
            continue
        seen.add(key)
        selected.append(it)
        if len(selected) >= limit:
            break

    return selected[:limit]


def _get_backup_status():
    data = get_backups()
    if not isinstance(data, dict) or data.get("error"):
        return {"state": "err", "reason": "not_found"}

    _FREQ_MAX_HOURS = {"daily": 48, "weekly": 216, "monthly": 792}  # 2d, 9d, 33d

    jobs = data.get("jobs") or []
    enabled_jobs = [j for j in jobs if j.get("enabled", True)]
    if not enabled_jobs:
        return {"state": "ok"}

    now = datetime.now()
    worst = None  # (age_hours, job_name)
    for job in enabled_jobs:
        last_backup = str(job.get("last_backup") or "").strip()
        if not last_backup:
            continue
        try:
            last_dt = datetime.strptime(last_backup, "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
        age_hours = int((now - last_dt).total_seconds() / 3600)
        freq = str((job.get("schedule") or {}).get("frequency") or "daily")
        max_hours = _FREQ_MAX_HOURS.get(freq, 48)
        if age_hours > max_hours:
            if worst is None or age_hours > worst[0]:
                worst = (age_hours, job.get("name", ""))

    if worst:
        return {"state": "warn", "age_hours": worst[0], "job": worst[1]}

    last_backup = str(data.get("last_backup") or "").strip()
    if last_backup:
        try:
            last_dt = datetime.strptime(last_backup, "%Y-%m-%d %H:%M:%S")
            age_hours = int((now - last_dt).total_seconds() / 3600)
            return {"state": "ok", "age_hours": age_hours}
        except Exception:
            pass
    return {"state": "ok"}

def get_system_info():
    try:
        hostname = platform.node()
        system = f"{platform.system()} {platform.release()}"

        uptime_seconds = time.time() - psutil.boot_time()
        days = int(uptime_seconds // 86400)
        hours = int((uptime_seconds % 86400) // 3600)
        minutes = int((uptime_seconds % 3600) // 60)
        uptime_parts = []
        if days:
            uptime_parts.append(f"{days} dias")
        uptime_parts.append(f"{hours}h")
        uptime_parts.append(f"{minutes}m")
        uptime = " ".join(uptime_parts)


        cpu_percent = psutil.cpu_percent(interval=0.5)

        ram = psutil.virtual_memory()
        ram_used = round(ram.used / (1024 ** 2))
        ram_total = round(ram.total / (1024 ** 2))
        ram_percent = int(ram.percent)

        iface = "N/A"
        ip = "N/A"
        mac = ""
        mask = ""
        for name, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if addr.family.name == "AF_INET" and not addr.address.startswith("127."):
                    iface = name
                    ip = addr.address
                    mask = addr.netmask or ""
                    break
            if iface != "N/A":
                for addr in addrs:
                    family = addr.family.name if hasattr(addr.family, "name") else str(addr.family)
                    if family in ("AF_LINK", "AF_PACKET") and not mac:
                        mac = addr.address
                break

        net_info = get_network_info()
        gateway = net_info.get("gateway", "")
        dns = ", ".join(net_info.get("dns", [])[:2])
        import time as _time
        if not hasattr(app, "_ddns_cache") or _time.time() - app._ddns_cache[0] > 60:
            from system.network import get_ddns_status as _ddns
            app._ddns_cache = (_time.time(), _ddns())
        _ddns_info = app._ddns_cache[1]
        public_ip = _ddns_info.get("public_ip", "")
        ddns_host = _ddns_info.get("hostname", "")

        disk = psutil.disk_usage("/")
        disk_used = round(disk.used / (1024 ** 3), 1)
        disk_total = round(disk.total / (1024 ** 3), 1)
        disk_percent = int(disk.percent)

        net = psutil.net_io_counters()
        net_rx = round(net.bytes_recv / (1024 ** 2), 2)  # MB
        net_tx = round(net.bytes_sent / (1024 ** 2), 2)  # MB

        disk_io = psutil.disk_io_counters()
        disk_read = disk_io.read_bytes if disk_io else 0
        disk_write = disk_io.write_bytes if disk_io else 0

        disk_io_per = psutil.disk_io_counters(perdisk=True)
        disk_io_map = {}
        if disk_io_per:
            for name, io in disk_io_per.items():
                disk_io_map[name] = {
                    "read": io.read_bytes,
                    "write": io.write_bytes
                }

        cpu_temp = None
        fan_rpm = None
        try:
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                cpu_temp = round(int(f.read().strip()) / 1000, 1)
        except Exception:
            cpu_temp = None

        try:
            import glob
            fan_files = glob.glob("/sys/class/hwmon/hwmon*/fan*_input")
            if fan_files:
                with open(fan_files[0], "r") as f:
                    fan_rpm = int(f.read().strip())
        except Exception:
            fan_rpm = None

        # Top procesos por CPU
        now = time.monotonic()
        cpu_count = psutil.cpu_count(logical=True) or 1
        seen_pids = set()
        processes = []

        for p in psutil.process_iter(['pid', 'name', 'create_time']):
            try:
                info = p.info
                pid = info.get('pid')
                if pid is None:
                    continue
                seen_pids.add(pid)

                name = info.get('name') or "unknown"

                # excluir proceso idle de Windows
                if name == "System Idle Process":
                    continue

                mem_mb = 0.0
                try:
                    mem_info = p.memory_info()
                    mem_mb = (mem_info.rss / (1024 ** 2)) if mem_info else 0.0
                except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError, TypeError):
                    mem_mb = 0.0

                proc_time = 0.0
                try:
                    cpu_times = p.cpu_times()
                    if cpu_times:
                        proc_time = float(getattr(cpu_times, "user", 0.0) or 0.0) + float(getattr(cpu_times, "system", 0.0) or 0.0)
                except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError, TypeError):
                    proc_time = 0.0

                create_time = info.get('create_time')
                prev = _PROC_CPU_CACHE.get(pid)
                cpu = 0.0
                if prev and (create_time is None or prev[0] is None or prev[0] == create_time):
                    _, prev_ts, prev_proc_time = prev
                    dt = now - prev_ts
                    if dt >= 0.25:
                        delta = proc_time - prev_proc_time
                        if delta > 0:
                            # Normaliza a 0..100 (psutil permite >100 en equipos multi-core)
                            cpu = (delta / dt) * 100.0 / cpu_count
                        if cpu < 0:
                            cpu = 0.0
                        if cpu > 100.0:
                            cpu = 100.0

                _PROC_CPU_CACHE[pid] = (create_time, now, proc_time)

                processes.append({
                    "name": name,
                    "cpu": round(cpu, 1),
                    "ram": round(mem_mb, 1)
                })

            except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError, AttributeError, TypeError):
                continue

        # fallback: si por permisos/attrs no se pudo leer nada, usa al menos top por RAM
        if not processes:
            seen_pids = set()
            for p in psutil.process_iter(['pid', 'name', 'memory_info']):
                try:
                    info = p.info
                    pid = info.get('pid')
                    if pid is None:
                        continue
                    seen_pids.add(pid)

                    name = info.get('name') or "unknown"
                    if name == "System Idle Process":
                        continue

                    mem_info = info.get('memory_info')
                    mem_mb = (mem_info.rss / (1024 ** 2)) if mem_info else 0.0

                    processes.append({
                        "name": name,
                        "cpu": 0.0,
                        "ram": round(mem_mb, 1)
                    })
                except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError, AttributeError, TypeError):
                    continue

        # ultimo recurso: al menos nombres (si memory_info tambien esta bloqueado)
        if not processes:
            seen_pids = set()
            for p in psutil.process_iter(['pid', 'name']):
                try:
                    info = p.info
                    pid = info.get('pid')
                    if pid is None:
                        continue
                    seen_pids.add(pid)

                    name = info.get('name') or "unknown"
                    if name == "System Idle Process":
                        continue

                    processes.append({
                        "name": name,
                        "cpu": 0.0,
                        "ram": 0.0
                    })
                except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError, AttributeError, TypeError):
                    continue

        # fallback extra: intenta usar 'ps' si psutil no pudo listar nada o devolvio muy pocos
        if len(processes) < 4:
            ps_procs = _get_top_processes_from_ps(limit=4)
            if ps_procs:
                processes = ps_procs

        # limpiar cache de PIDs que ya no existen
        for pid in list(_PROC_CPU_CACHE.keys()):
            if pid not in seen_pids:
                _PROC_CPU_CACHE.pop(pid, None)

        processes = sorted(processes, key=lambda x: (x["cpu"], x["ram"]), reverse=True)[:4]
        while len(processes) < 4:
            processes.append({"name": "-", "cpu": 0.0, "ram": 0.0})
        for _p in processes:
            _p["device"] = device_in_text(_p.get("name", ""))  # p.ej. smbd[10.0.0.2] -> nombre del movil
        services = get_services_status()
        disks = get_disks()
        disks_tree = get_disks_tree_all()
        backup = _get_backup_status()

        sysinfo = {
            "hostname": hostname,
            "system": system,
            "uptime": uptime,
            "cpu_percent": cpu_percent,
            "ram_used": ram_used,
            "ram_total": ram_total,
            "ram_percent": ram_percent,
            "iface": iface,
            "ip": ip,
            "mac": mac,
            "mask": mask,
            "gateway": gateway,
            "dns": dns,
            "disk_used": disk_used,
            "disk_total": disk_total,
            "disk_percent": disk_percent,
            "net_rx": net_rx,
            "net_tx": net_tx,
            "public_ip": public_ip,
            "ddns_host": ddns_host,
            "disk_read": disk_read,
            "disk_write": disk_write,
            "disk_io": disk_io_map,
            "cpu_temp": cpu_temp,
            "fan_rpm": fan_rpm,
            "processes": processes,
            "services": services,            "disks": disks,
            "disks_tree": disks_tree,
            "backup": backup
        }
        sysinfo["alerts"] = get_alerts(sysinfo)
        return sysinfo



    except Exception as e:
        return {"error": str(e)}

@app.route("/")
@login_required
def dashboard():
    return render_template("dashboard.html")

@app.route("/api/system")
@login_required
def api_system():
    return jsonify(get_system_info())

@app.route("/api/network")
@login_required
def api_network():
    return jsonify(get_network_info())

@app.route("/api/network/ddns")
@login_required
@require_role("operator")
def api_network_ddns():
    from system.network import get_ddns_status
    return jsonify(get_ddns_status())

@app.route("/api/disks/smart")
@login_required
def api_disks_smart():
    from system.smart import get_smart
    device = request.args.get("device", "").strip()
    if not device or not device.startswith("/dev/"):
        return jsonify({"error": "dispositivo invalido"})
    return jsonify(get_smart(device))

@app.route("/api/disks/smart/schedule")
@login_required
def api_disks_smart_schedule_get():
    from system.smart import get_schedule
    return jsonify(get_schedule())

@app.route("/api/disks/smart/schedule", methods=["POST"])
@login_required
@require_role("admin")
def api_disks_smart_schedule_set():
    from system.smart import set_schedule
    data = request.get_json(silent=True) or {}
    return jsonify(set_schedule(
        data.get("enabled", False),
        data.get("weekday", 5),
        data.get("time", "10:00"),
    ))

@app.route("/api/disks/smart/test", methods=["POST"])
@login_required
@require_role("admin")
def api_disks_smart_test():
    from system.smart import start_long_test
    data = request.get_json(silent=True) or {}
    device = str(data.get("device", "")).strip()
    if device and not device.startswith("/dev/"):
        return jsonify({"ok": False, "error": "dispositivo invalido"})
    return jsonify(start_long_test(device))

@app.route("/api/disks/mount", methods=["POST"])
@login_required
@require_role("admin")
def api_disks_mount():
    data = request.get_json(silent=True) or {}
    device = (data.get("device") or "").strip()
    mountpoint = (data.get("mountpoint") or "").strip()
    return jsonify(mount_partition(device, mountpoint))

@app.route("/api/disks/unmount", methods=["POST"])
@login_required
@require_role("admin")
def api_disks_unmount():
    data = request.get_json(silent=True) or {}
    target = (data.get("target") or "").strip()
    return jsonify(unmount_partition(target))

@app.route("/api/users")
@login_required
@require_role("operator")
def api_users():
    return jsonify(get_users())

@app.route("/api/logs")
@login_required
@require_role("operator")
def api_logs():
    filter_type = request.args.get("filter", "all")
    query = request.args.get("q", "")
    try:
        limit = int(request.args.get("limit", "200"))
    except ValueError:
        limit = 200
    return jsonify(get_logs(filter_type=filter_type, query=query, limit=limit))

@app.route("/api/updates")
@login_required
def api_updates():
    return jsonify(get_updates())

@app.route("/api/updates/run", methods=["POST"])
@login_required
@require_role("admin")
def api_updates_run():
    return jsonify(run_updates())

def _backup_en_marcha_error(accion):
    """Texto de error si hay backups corriendo (apagar/reiniciar los cortaría); None si no hay."""
    names = get_running_job_names()
    if not names:
        return None
    if len(names) == 1:
        return f"No se puede {accion}: hay un backup en marcha ({names[0]}). Espera a que termine o cancélalo antes."
    return f"No se puede {accion}: hay {len(names)} backups en marcha ({', '.join(names)}). Espera a que terminen o cancélalos antes."

@app.route("/api/system/reboot", methods=["POST"])
@login_required
@require_role("admin")
def api_system_reboot():
    err = _backup_en_marcha_error("reiniciar el NAS")
    if err:
        return jsonify({"error": err})
    return jsonify(reboot_system())



@app.route("/api/system/shutdown", methods=["POST"])
@login_required
def api_system_shutdown():
    if session.get('role') != 'admin':
        return jsonify({'error': 'Solo administradores'}), 403
    err = _backup_en_marcha_error("apagar el NAS")
    if err:
        return jsonify({"error": err})
    return jsonify(shutdown_system())

@app.route("/api/system/restart-panel", methods=["POST"])
@login_required
@require_role("admin")
def api_system_restart_panel():
    err = _backup_en_marcha_error("reiniciar el panel")
    if err:
        return jsonify({"error": err})
    return jsonify(restart_panel_service())

# --- Kernel del NAS: preparar y activar un kernel de bodhi (ver system/kernel.py). Solo admin ---
@app.route("/api/kernel/status")
@login_required
@require_role("admin")
def api_kernel_status():
    return jsonify(kernel_status())

@app.route("/api/kernel/job")
@login_required
@require_role("admin")
def api_kernel_job():
    return jsonify(kernel_job())

@app.route("/api/kernel/upload", methods=["POST"])
@login_required
@require_role("admin")
def api_kernel_upload():
    # Cuerpo en bruto (no multipart) para no cargar 40 MB en memoria; nombre y sha256 van en cabeceras.
    return jsonify(kernel_save_upload(request.stream, request.headers.get("X-Filename", ""),
                                      request.headers.get("X-Sha256", ""), request.content_length))

@app.route("/api/kernel/prepare", methods=["POST"])
@login_required
@require_role("admin")
def api_kernel_prepare():
    err = _backup_en_marcha_error("preparar el kernel")
    if err:
        return jsonify({"error": err})
    return jsonify(kernel_start_prepare())

@app.route("/api/kernel/activate", methods=["POST"])
@login_required
@require_role("admin")
def api_kernel_activate():
    err = _backup_en_marcha_error("activar el kernel")
    if err:
        return jsonify({"error": err})
    data = request.get_json(silent=True) or {}
    return jsonify(kernel_activate(str(data.get("version", ""))))

@app.route("/api/kernel/discard", methods=["POST"])
@login_required
@require_role("admin")
def api_kernel_discard():
    return jsonify(kernel_discard())

@app.route("/api/system/ping")
@login_required
def api_system_ping():
    # Endpoint deliberadamente barato (sin psutil pesado) para que el overlay
    # de reinicio del panel pueda sondear cada 1-2s sin coste. Devuelve el PID
    # de este proceso para que el cliente detecte un reinicio REAL (PID
    # distinto) en vez de fiarse de cualquier 200 OK, que podria venir del
    # proceso viejo todavia vivo un instante tras el click.
    return jsonify({"ok": True, "pid": _PANEL_PID})

@app.route("/api/power/status")
@login_required
def api_power_status():
    return jsonify(get_power_status())

@app.route("/api/power/schedule", methods=["POST"])
@login_required
@require_role("admin")
def api_power_schedule():
    data = request.get_json(silent=True) or {}
    return jsonify(set_schedule(
        data.get("enabled", False),
        data.get("shutdown_time", "02:00"),
        data.get("days", "all"),
    ))

def _redact_backups(data):
    """Solo admin recibe las contrasenas/config WG de los jobs; operator las ve vacias (con el flag *_set)."""
    if (session.get("role") or "viewer").lower() == "admin":
        return data
    return mask_secrets(data)

@app.route("/api/backups")
@login_required
def api_backups():
    data = get_backups()
    role = (session.get("role") or "viewer").lower()
    if _ROLE_ORDER.get(role, 0) < _ROLE_ORDER["operator"] and isinstance(data, dict):
        # Un viewer ve el estado de los jobs, no su configuracion (hosts, usuarios y contrasenas SMB/SSH).
        data = dict(data)
        data["jobs"] = [
            {k: v for k, v in j.items() if k not in ("smb", "ssh", "local")}
            for j in (data.get("jobs") or []) if isinstance(j, dict)
        ]
    return jsonify(_redact_backups(data))

@app.route("/api/backups/run", methods=["POST"])
@login_required
@require_role("operator")
def api_backups_run():
    data = request.get_json(silent=True) or {}
    from system.backups import run_backup
    return jsonify(run_backup(data.get("job_id")))

@app.route("/api/backups/config", methods=["POST"])
@login_required
@require_role("operator")
def api_backups_config():
    data = request.get_json(silent=True) or {}
    from system.backups import save_backup_config
    keep = (session.get("role") or "viewer").lower() != "admin"
    return jsonify(_redact_backups(save_backup_config(data, keep_secrets=keep)))


@app.route("/api/backups/jobs/create", methods=["POST"])
@login_required
@require_role("operator")
def api_backups_jobs_create():
    data = request.get_json(silent=True) or {}
    from system.backups import create_backup_job
    return jsonify(_redact_backups(create_backup_job(data)))


@app.route("/api/backups/jobs/update", methods=["POST"])
@login_required
@require_role("operator")
def api_backups_jobs_update():
    data = request.get_json(silent=True) or {}
    from system.backups import update_backup_job
    keep = (session.get("role") or "viewer").lower() != "admin"
    return jsonify(_redact_backups(update_backup_job(data, keep_secrets=keep)))


@app.route("/api/backups/jobs/delete", methods=["POST"])
@login_required
@require_role("operator")
def api_backups_jobs_delete():
    data = request.get_json(silent=True) or {}
    from system.backups import delete_backup_job
    return jsonify(delete_backup_job(data.get("id")))


@app.route("/api/backups/select", methods=["POST"])
@login_required
@require_role("operator")
def api_backups_select():
    data = request.get_json(silent=True) or {}
    from system.backups import select_backup_job
    return jsonify(_redact_backups(select_backup_job(data.get("id"))))

@app.route("/api/backups/progress/<job_id>")
@login_required
@require_role("operator")
def api_backup_progress(job_id):
    return jsonify(get_backup_progress(job_id))


@app.route("/api/backups/log/<job_id>")
@login_required
@require_role("operator")
def api_backup_log(job_id):
    return jsonify(get_backup_log(job_id))


@app.route("/api/backups/ssh-pubkey")
@login_required
@require_role("operator")
def api_backup_ssh_pubkey():
    key_path = request.args.get("key_path", "/root/.ssh/id_rsa").strip()
    return jsonify(get_ssh_pubkey(key_path))



@app.route("/api/backups/ssh-keygen", methods=["POST"])
@login_required
@require_role("operator")
def api_backup_ssh_keygen():
    key_path = (request.get_json(silent=True) or {}).get("key_path", "/root/.ssh/id_rsa").strip()
    return jsonify(generate_ssh_key(key_path))

@app.route("/api/backups/pc/browse")
@login_required
@require_role("operator")
def api_backup_pc_browse():
    job_id = request.args.get("job_id", "").strip()
    path = request.args.get("path", "C:\\").strip()
    return jsonify(browse_pc_dirs(job_id, path))


@app.route("/api/fs/list")
@login_required
@require_role("operator")
def api_fs_list():
    path = request.args.get("path", "/data")
    return jsonify(list_dir(path))


@app.route("/api/fs/mkdir", methods=["POST"])
@login_required
@require_role("admin")
def api_fs_mkdir():
    data = request.get_json(silent=True) or {}
    parent = (data.get("parent") or "").strip()
    name = (data.get("name") or "").strip()
    return jsonify(create_dir(parent, name))


@app.route("/api/fs/rmdir", methods=["POST"])
@login_required
@require_role("admin")
def api_fs_rmdir():
    data = request.get_json(silent=True) or {}
    path = (data.get("path") or "").strip()
    return jsonify(remove_dir(path))

@app.route("/api/users/create", methods=["POST"])
@login_required
@require_role("admin")
def api_users_create():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    password = data.get("password") or ""
    admin = bool(data.get("admin"))
    groups = data.get("groups") or []
    return jsonify(create_user(name, password=password, admin=admin, groups=groups))

@app.route("/api/users/update", methods=["POST"])
@login_required
@require_role("admin")
def api_users_update():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    password = data.get("password") or None
    admin = data.get("admin")
    groups = data.get("groups")
    active = data.get("active")
    return jsonify(update_user(name, password=password, admin=admin, groups=groups, active=active))

@app.route("/api/users/delete", methods=["POST"])
@login_required
@require_role("admin")
def api_users_delete():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    remove_home = bool(data.get("remove_home", True))
    remove_personal_shares = bool(data.get("remove_personal_shares", False))
    remove_share_paths = bool(data.get("remove_share_paths", False))
    return jsonify(delete_user(
        name,
        remove_home=remove_home,
        remove_personal_shares=remove_personal_shares,
        remove_share_paths=remove_share_paths
    ))

@app.route("/api/mounts")
@login_required
@require_role("operator")
def api_mounts():
    return jsonify(list_mounts())

@app.route("/api/mounts/add", methods=["POST"])
@login_required
@require_role("admin")
def api_mounts_add():
    data = request.get_json(silent=True) or {}
    host = (data.get("host") or "").strip()
    share = (data.get("share") or "").strip()
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    mountpoint = (data.get("mountpoint") or "").strip()
    vers = (data.get("vers") or "3.0").strip()
    return jsonify(mount_smb(host, share, username, password, mountpoint, vers))

@app.route("/api/mounts/remove", methods=["POST"])
@login_required
@require_role("admin")
def api_mounts_remove():
    data = request.get_json(silent=True) or {}
    mountpoint = (data.get("mountpoint") or "").strip()
    return jsonify(unmount(mountpoint))

@app.route("/api/services/<name>")
@login_required
def api_service_detail(name):
    return jsonify(get_service_detail(name))

@app.route("/api/services/<name>/restart", methods=["POST"])
@login_required
@require_role("operator")
def api_service_restart(name):
    return jsonify(restart_service(name))

@app.route("/api/services/<name>/stop", methods=["POST"])
@login_required
@require_role("admin")
def api_service_stop(name):
    return jsonify(stop_service(name))

@app.route("/api/services/firewall/enable", methods=["POST"])
@login_required
@require_role("admin")
def api_firewall_enable():
    return jsonify(enable_firewall())

@app.route("/api/services/firewall/disable", methods=["POST"])
@login_required
@require_role("admin")
def api_firewall_disable():
    return jsonify(disable_firewall())


@app.route("/api/notifications")
@login_required
@require_role("admin")
def api_notifications_get():
    from system.notifications import get_config
    return jsonify(get_config())

@app.route("/api/notifications", methods=["POST"])
@login_required
@require_role("admin")
def api_notifications_save():
    from system.notifications import save_config
    data = request.get_json(silent=True) or {}
    return jsonify(save_config(data))

@app.route("/api/notifications/test", methods=["POST"])
@login_required
@require_role("admin")
def api_notifications_test():
    from system.notifications import test_notification
    return jsonify(test_notification())

@app.route("/api/groups")
@login_required
@require_role("operator")
def api_groups():
    return jsonify(get_groups())

@app.route("/api/groups/create", methods=["POST"])
@login_required
@require_role("admin")
def api_groups_create():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    return jsonify(create_group(name))

@app.route("/api/groups/add-user", methods=["POST"])
@login_required
@require_role("admin")
def api_groups_add_user():
    data = request.get_json(silent=True) or {}
    user = (data.get("user") or "").strip()
    group = (data.get("group") or "").strip()
    return jsonify(add_user_to_group(user, group))

@app.route("/api/groups/remove-user", methods=["POST"])
@login_required
@require_role("admin")
def api_groups_remove_user():
    data = request.get_json(silent=True) or {}
    user = (data.get("user") or "").strip()
    group = (data.get("group") or "").strip()
    return jsonify(remove_user_from_group(user, group))


@app.route("/api/shares")
@login_required
@require_role("operator")
def api_shares():
    return jsonify(list_shares())


@app.route("/api/shares/create", methods=["POST"])
@login_required
@require_role("admin")
def api_shares_create():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    path = (data.get("path") or "").strip()
    read_only = bool(data.get("read_only"))
    users = data.get("users") or []
    groups = data.get("groups") or []
    return jsonify(create_share(name, path, read_only=read_only, users=users, groups=groups))


@app.route("/api/shares/update", methods=["POST"])
@login_required
@require_role("admin")
def api_shares_update():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    path = (data.get("path") or "").strip()
    read_only = bool(data.get("read_only"))
    users = data.get("users") or []
    groups = data.get("groups") or []
    return jsonify(update_share(name, path, read_only=read_only, users=users, groups=groups))


@app.route("/api/shares/delete", methods=["POST"])
@login_required
@require_role("admin")
def api_shares_delete():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    return jsonify(delete_share(name))


@app.route("/api/shares/reload", methods=["POST"])
@login_required
@require_role("admin")
def api_shares_reload():
    return jsonify(reload_samba())


# -------------------------------------------------------
# WireGuard API
# -------------------------------------------------------
@app.route("/api/wg/status")
@login_required
@require_role("operator")
def api_wg_status():
    return jsonify(wg_mod.get_status())

@app.route("/api/wg/init", methods=["POST"])
@login_required
@require_role("admin")
def api_wg_init():
    data = request.get_json(silent=True) or {}
    endpoint = (data.get("endpoint") or "").strip()
    if not endpoint:
        return jsonify({"error": "endpoint requerido"}), 400
    return jsonify(wg_mod.init_server(endpoint))

@app.route("/api/wg/start", methods=["POST"])
@login_required
@require_role("admin")
def api_wg_start():
    return jsonify(wg_mod.start_wg())

@app.route("/api/wg/stop", methods=["POST"])
@login_required
@require_role("admin")
def api_wg_stop():
    return jsonify(wg_mod.stop_wg())

@app.route("/api/wg/endpoint", methods=["POST"])
@login_required
@require_role("admin")
def api_wg_endpoint():
    data = request.get_json(silent=True) or {}
    endpoint = (data.get("endpoint") or "").strip()
    if not endpoint:
        return jsonify({"error": "endpoint requerido"}), 400
    return jsonify(wg_mod.save_endpoint(endpoint))

@app.route("/api/wg/peer/add", methods=["POST"])
@login_required
@require_role("admin")
def api_wg_peer_add():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    return jsonify(wg_mod.add_peer(name))

@app.route("/api/wg/peer/delete", methods=["POST"])
@login_required
@require_role("admin")
def api_wg_peer_delete():
    data = request.get_json(silent=True) or {}
    pubkey = (data.get("pubkey") or "").strip()
    if not pubkey:
        return jsonify({"error": "pubkey requerido"}), 400
    return jsonify(wg_mod.delete_peer(pubkey))

@app.route("/api/wg/peer/rename", methods=["POST"])
@login_required
@require_role("admin")
def api_wg_peer_rename():
    data = request.get_json(silent=True) or {}
    pubkey = (data.get("pubkey") or "").strip()
    name   = (data.get("name") or "").strip()
    if not pubkey:
        return jsonify({"error": "pubkey requerido"}), 400
    return jsonify(wg_mod.rename_peer(pubkey, name))



@app.route("/api/wol")
@login_required
@require_role("operator")
def api_wol_get():
    return jsonify(get_wol_targets())

@app.route("/api/wol", methods=["POST"])
@login_required
@require_role("operator")
def api_wol_send():
    data = request.get_json(silent=True) or {}
    mac = (data.get("mac") or "").strip()
    if not mac:
        return jsonify({"error": "mac requerido"}), 400
    return jsonify(send_wol(mac))

@app.route("/api/wol/targets", methods=["POST"])
@login_required
@require_role("operator")
def api_wol_add():
    data = request.get_json(silent=True) or {}
    return jsonify(add_wol_target(data.get('name',''), data.get('mac',''), data.get('ip','')))

@app.route("/api/wol/targets/edit", methods=["POST"])
@login_required
@require_role("operator")
def api_wol_edit():
    data = request.get_json(silent=True) or {}
    old_name = (data.get('old_name') or '').strip()
    if not old_name:
        return jsonify({'ok': False, 'error': 'old_name requerido'})
    return jsonify(edit_wol_target(old_name, data.get('name',''), data.get('mac',''), data.get('ip','')))

@app.route("/api/wol/targets/delete", methods=["POST"])
@login_required
@require_role("operator")
def api_wol_delete():
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'ok': False, 'error': 'name requerido'})
    return jsonify(delete_wol_target(name))

@app.route("/api/wol/shutdown", methods=["POST"])
@login_required
@require_role("admin")
def api_wol_shutdown():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "name requerido"})
    return jsonify(wol_shutdown(name))

@app.route("/api/fail2ban")
@login_required
@require_role("operator")
def api_fail2ban():
    return jsonify(get_fail2ban_status())

@app.route("/api/fail2ban/unban", methods=["POST"])
@login_required
@require_role("admin")
def api_fail2ban_unban():
    data = request.get_json(silent=True) or {}
    jail = (data.get("jail") or "").strip()
    ip = (data.get("ip") or "").strip()
    if not jail or not ip:
        return jsonify({"error": "jail e ip requeridos"}), 400
    return jsonify(fail2ban_unban_ip(jail, ip))

if sock:
    @sock.route("/ws/ssh")
    def ws_ssh(ws):
        if not session.get("logged_in"):
            try:
                ws.send(json.dumps({"type": "error", "error": "unauthorized"}))
            except Exception:
                pass
            return
        if _ROLE_ORDER.get((session.get("role") or "viewer").lower(), 0) < _ROLE_ORDER["admin"]:
            try:
                ws.send(json.dumps({"type": "error", "error": "admin_only"}))
            except Exception:
                pass
            return

        creds = _get_creds()
        username = creds.get("username") if creds else None
        password = creds.get("password") if creds else None

        def send(obj):
            ws.send(json.dumps(obj))

        send({"type": "status", "text": "Conectando..."})

        if not username or not password:
            send({"type": "need_auth"})
            msg = None
            while True:
                raw = ws.receive()
                if not raw:
                    return
                try:
                    m = json.loads(raw)
                except Exception:
                    continue
                if m.get("type") == "auth":
                    msg = m
                    break
                # ignore resize/input until auth arrives
            username = (msg.get("username") or "").strip()
            password = msg.get("password") or ""
            ok, err = _ssh_check_local(username, password)
            if not ok:
                send({"type": "error", "error": err or "auth failed"})
                return
            session["username"] = username
            _set_creds(username, password)

        host = os.environ.get("IX2PANEL_SSH_HOST", "127.0.0.1")
        port = int(os.environ.get("IX2PANEL_SSH_PORT", "22"))

        if not paramiko:
            send({"type": "error", "error": "SSH no disponible (paramiko no instalado)"})
            return

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=host,
                port=port,
                username=username,
                password=password,
                # Hardware ARM antiguo: el hash PAM de la contrasena puede tardar
                # varios segundos, 5s se quedaba corto y disparaba "Authentication timeout".
                timeout=8,
                auth_timeout=20,
                banner_timeout=20,
                allow_agent=False,
                look_for_keys=False,
            )
            chan = client.invoke_shell(term="xterm")
        except Exception as e:
            send({"type": "error", "error": str(e)})
            try:
                client.close()
            except Exception:
                pass
            return

        stop = threading.Event()

        def pump_ssh_to_ws():
            try:
                while not stop.is_set():
                    if chan.recv_ready():
                        data = chan.recv(4096)
                        if not data:
                            break
                        send({"type": "data", "data": data.decode("utf-8", errors="replace")})
                    else:
                        time.sleep(0.01)
            except Exception:
                pass
            finally:
                stop.set()

        t = threading.Thread(target=pump_ssh_to_ws, daemon=True)
        t.start()

        send({"type": "status", "text": "Conectado"})

        try:
            while not stop.is_set():
                raw = ws.receive()
                if raw is None:
                    break
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue

                mtype = msg.get("type")
                if mtype == "input":
                    data = msg.get("data") or ""
                    try:
                        chan.send(data)
                    except Exception:
                        break
                elif mtype == "resize":
                    try:
                        cols = int(msg.get("cols") or 0)
                        rows = int(msg.get("rows") or 0)
                        if cols > 0 and rows > 0:
                            chan.resize_pty(width=cols, height=rows)
                    except Exception:
                        pass
        finally:
            stop.set()
            try:
                chan.close()
            except Exception:
                pass
            try:
                client.close()
            except Exception:
                pass



if __name__ == "__main__":
    # threaded=True es imprescindible: sin esto, el servidor de desarrollo de
    # Werkzeug atiende una conexion a la vez. La terminal web mantiene un
    # WebSocket abierto de forma indefinida, y sin hilos ese unico worker
    # queda bloqueado, provocando que peticiones HTTP normales reutilizando
    # la misma conexion persistente hacia nginx se entrelacen con los frames
    # del WebSocket ("Invalid frame header" en el navegador).
    app.run(host="127.0.0.1", port=8080, debug=False, use_reloader=False, threaded=True)


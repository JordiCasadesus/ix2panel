# ix2Panel

Panel de administración web para el NAS doméstico (Iomega IX2 / Debian Linux). Permite gestionar discos, backups, usuarios, compartidas SMB, VPN WireGuard, servicios, logs y más, todo desde el navegador.

> **Por qué existe:** el Lenovo/Iomega ix2-ng es un ARM `armv5tel` con 234 MB de RAM, donde Cockpit, OpenMediaVault o Webmin no caben o van muy justos. Este panel es ligero (Flask + JavaScript puro) y está pensado para ese hardware.
>
> **Aviso:** es un proyecto personal. El panel corre como **root** y tiene Terminal SSH, firewall y actualización de kernel, así que está pensado para usarse en la **LAN o por VPN**, no para exponerlo directamente a internet. Si lo expones (nginx + HTTPS), hazlo asumiendo el riesgo. Diseñado, probado y usado a diario por Jordi Casadesús en un Lenovo IX2-ng real; el código se ha escrito con ayuda de Claude (Anthropic). Los ejemplos de este README (dominio, IPs, nombres de jobs) son los del autor: cámbialos por los tuyos. Licencia [PolyForm Noncommercial 1.0.0](LICENSE): libre para usar, copiar y modificar, **no para venderlo ni para uso comercial**.

---

## Índice

1. [Descripción general](#descripción-general)
2. [Hardware del NAS](#hardware-del-nas)
3. [Stack tecnológico](#stack-tecnológico)
4. [Estructura del proyecto](#estructura-del-proyecto)
5. [Instalación en el NAS](#instalación-en-el-nas)
6. [Desarrollo en Windows](#desarrollo-en-windows)
7. [Ficheros de configuración y su ubicación en el NAS](#ficheros-de-configuración-y-su-ubicación-en-el-nas)
8. [Sistema de backups](#sistema-de-backups)
9. [SSH desde el NAS al PC Windows](#ssh-desde-el-nas-al-pc-windows)
10. [Notificaciones Telegram](#notificaciones-telegram)
11. [WireGuard VPN](#wireguard-vpn)
12. [Acceso remoto por HTTPS (nginx + DDNS)](#acceso-remoto-por-https-nginx--ddns)
13. [Terminal SSH integrada](#terminal-ssh-integrada)
14. [Compartidas SMB](#compartidas-smb)
15. [Roles y autenticación](#roles-y-autenticación)
16. [Servicio systemd (Gunicorn)](#servicio-systemd-gunicorn)
17. [Módulos del sistema](#módulos-del-sistema)
18. [Solución de problemas](#solución-de-problemas)
19. [Contacto y colaboración](#contacto-y-colaboración)

---

## Descripción general

ix2Panel corre en el NAS como un servicio systemd y expone un panel web en el puerto **8080**, servido por **Gunicorn** (no por el servidor de desarrollo de Flask — ver [Servicio systemd](#servicio-systemd-gunicorn)).

Acceso normal: **`https://tu-dominio.ddns.net`** (nginx hace de proxy inverso hacia el puerto 8080 — ver [Acceso remoto](#acceso-remoto-por-https-nginx--ddns)), tanto desde dentro como desde fuera de la LAN. El acceso directo `http://IP_DEL_NAS:8080` no es fiable — ver nota en esa misma sección.

El panel está diseñado para ejecutarse con el usuario **root** en el NAS para tener acceso completo a systemctl, montajes, usuarios, discos y backups.

---

## Hardware del NAS

El Iomega IX2 es un dispositivo **ARM de gama muy baja** (`armv5tel`) con solo **234 MB de RAM** — nada que ver con un PC o una Raspberry Pi moderna. Esto tiene implicaciones prácticas reales:

- Cualquier paquete Python sin wheel precompilado para `armv5tel` (p.ej. `greenlet`) se compila desde cero al instalar — puede tardar varios minutos.
- El hash de contraseña (PAM/crypt) para login SSH tarda más de lo habitual — por eso los timeouts de paramiko están subidos a 20s en vez de los 5s por defecto (ver [Terminal SSH integrada](#terminal-ssh-integrada)).
- Con poca RAM libre, el sistema empieza a usar swap fácilmente bajo carga (backups grandes, varias conexiones SSH simultáneas...) y todo — panel incluido — se nota más lento. No es un fallo del código, es limitación física del hardware.
- Transferencias grandes (backups de cientos de GB) pueden tardar horas y consumir CPU real de forma sostenida (`smbd` al 30% durante horas es normal en una transferencia grande).

---

## Stack tecnológico

| Capa | Tecnología |
|---|---|
| Backend | Python 3.13 + Flask 3.1.2 |
| Servidor WSGI | **Gunicorn 23.0.0**, worker **eventlet 0.41.2** (imprescindible para el WebSocket — ver más abajo) |
| WebSocket (terminal SSH) | Flask-Sock 0.7.0 + simple-websocket 1.1.0 |
| Métricas del sistema | psutil 7.2.1 |
| SSH remoto (backups y terminal) | paramiko 3.5.1 |
| QR codes (WireGuard) | qrcode 8.2 |
| HTTP externo | requests 2.32.3 |
| Reverse proxy / HTTPS | nginx + Let's Encrypt (DDNS `tu-dominio.ddns.net`) |
| Frontend | HTML5 + CSS3 + JavaScript puro |
| Terminal web | xterm.js real (vendorizado, carga perezosa — ver [Terminal SSH integrada](#terminal-ssh-integrada)) |

Dependencias Python completas en `requirements.txt`.

> **Importante:** `flask-sock`/`simple-websocket` usan hilos reales (`threading.Thread`) para leer del socket. Bajo Gunicorn con worker `eventlet`, hace falta `eventlet.monkey_patch()` **como primera línea del proceso**, antes de cualquier otro import — ya está hecho al principio de `ix2panel.py`. Si algún día se toca ese fichero, esa línea debe seguir siendo la primera.

---

## Estructura del proyecto

```
ix2Panel/
├── ix2panel.py                  # App Flask principal — todas las rutas API y WebSocket
│                                 # (eventlet.monkey_patch() es literalmente la primera linea del fichero)
├── config.py                    # Configuración básica (rutas de backup locales)
├── requirements.txt             # Dependencias Python (incluye gunicorn + eventlet)
├── .secret_key                  # Clave de sesión Flask (autogenerada al primer arranque)
├── .panel_auth.json             # Credenciales del panel (hash PBKDF2/scrypt) — NO en git
│
├── system/                      # Módulos backend
│   ├── backups.py               # Jobs de backup, scheduler, rsync, SSH/robocopy
│   ├── disks.py                 # Particiones, SMART, montar/desmontar
│   ├── services.py              # Control de servicios systemd
│   ├── users.py                 # Usuarios Linux + Samba
│   ├── groups.py                # Grupos Linux
│   ├── shares.py                # Compartidas Samba (smb.conf) + ACL de transito para subcarpetas
│   ├── wireguard.py             # Servidor WireGuard, peers, QR codes
│   ├── network.py               # Info de red, DDNS
│   ├── notifications.py         # Notificaciones Telegram + monitor de eventos
│   ├── alerts.py                # Alertas automáticas (disco lleno, temp alta...)
│   ├── logs.py                  # Lectura journalctl
│   ├── power.py                 # Apagado programado, reboot
│   ├── updates.py               # Actualizaciones apt
│   ├── kernel.py                # Preparar/activar un kernel de bodhi (Sistema → Kernel del NAS)
│   ├── wol.py                   # Wake-on-LAN
│   ├── fail2ban.py              # Fail2Ban — jails e IPs baneadas
│   ├── smart.py                 # Datos SMART de discos
│   ├── mounts.py                # Montaje de unidades SMB/NFS
│   ├── devices.py               # IP -> nombre de dispositivo (peers WireGuard y WoL), solo para mostrar en logs
│   ├── fs.py                    # Explorador de ficheros básico
│   ├── firmware.py              # Gestión de firmware
│   └── backup.py                # Utilidades auxiliares de backup
│
├── templates/                   # Plantillas Jinja2
│   ├── layout.html              # Layout base (sidebar, navbar)
│   ├── login.html
│   ├── dashboard.html
│   └── tabs/                    # Una plantilla por pestaña (incluye terminal.html)
│
├── static/
│   ├── favicon.svg              # Icono de la pestaña (enlazado en layout.html; NO se sube solo por SFTP: usar SFTP: Upload)
│   ├── css/
│   │   ├── core.css
│   │   ├── polish.css           # Capa de mejoras visuales sobre core.css (borrar + su <link> en layout.html para revertir)
│   │   └── tabs/                # CSS específico por módulo (incluye terminal.css)
│   ├── js/
│   │   ├── core.js              # Lógica principal, polling, cambio de pestañas
│   │   ├── polish.js            # Accesibilidad de la barra de pestañas (teclado/roles) e indicador "Cargando datos del NAS..." del dashboard (lo quita core.js al llegar /api/system)
│   │   ├── path_tree.js         # Componente árbol de directorios (lazy, cualquier profundidad)
│   │   ├── role.js              # window.IX2_ROLE desde el atributo data-role del <body> (se carga el primero)
│   │   ├── login.js             # Mostrar/ocultar contraseña del login
│   │   └── tabs/                # JS específico por módulo (incluye terminal.js)
│   └── vendor/
│       └── xterm/                # xterm.js real (~283KB) — se carga solo al abrir la pestana Terminal,
│                                  # no en cada carga del panel (ver terminal.js: _loadXtermAssets)
│
└── .vscode/
    ├── launch.json              # Debug Flask en VS Code
    ├── sftp.json                # Sincronización automática con el NAS via SFTP
    └── tasks.json
```

> **Nota sobre permisos SFTP:** el usuario `TU_USUARIO` sincroniza por SFTP. Cualquier carpeta que quede propiedad de `root` (p.ej. si en algún momento se despliega algo con `scp` como root) bloqueará silenciosamente la subida de ficheros ahí — sin error visible en VS Code. Si un fichero no llega al NAS tras guardarlo, comprobar `ls -la` del directorio destino.

---

## Instalación en el NAS

### Requisitos previos

```bash
apt install python3 python3-venv python3-pip rsync samba wireguard fail2ban acl nginx
```

`acl` es necesario para `setfacl`/`getfacl` (compartidas anidadas — ver [Compartidas SMB](#compartidas-smb)). `nginx` solo si se quiere acceso remoto por HTTPS.

### Despliegue

```bash
# Copiar el proyecto al NAS
scp -r ix2Panel/ root@IP_DEL_NAS:/etc/ix2panel

# En el NAS, crear el entorno virtual
cd /etc/ix2panel
python3 -m venv venv
venv/bin/pip install -r requirements.txt

# Instalar el servicio systemd (ver seccion "Servicio systemd (Gunicorn)")
systemctl enable ix2panel
systemctl start ix2panel
```

> En este hardware ARM, `pip install` compila `greenlet` desde cero (no hay wheel precompilado) — puede tardar varios minutos, es normal.

### Sincronización desde VS Code (desarrollo activo)

El fichero `.vscode/sftp.json` configura la sincronización automática con el NAS:

- **Host:** `IP_DEL_NAS`
- **Usuario:** `TU_USUARIO`
- **Clave privada:** `C:\Users\TU_USUARIO\.ssh\ix2panel_key`
- **Ruta remota:** `/etc/ix2panel`
- **Auto-upload al guardar:** activado para `*.py`, `*.html`, `*.css`, `*.js`

Con la extensión SFTP de VS Code, cada vez que se guarda un fichero se sube automáticamente al NAS.

---

## Desarrollo en Windows

**El panel no se ejecuta en Windows.** El PC solo se usa como editor: el código vive en `C:\ruta\a\ix2Panel`, se edita en VS Code y la extensión SFTP lo sube al NAS al guardar. La ejecución y las pruebas son siempre en el NAS.

`python ix2panel.py` en Windows falla inmediatamente con `ModuleNotFoundError: No module named 'grp'`, antes de arrancar Flask: `ix2panel.py` importa `system/users.py`, `system/groups.py` y `system/shares.py`, y esos módulos hacen `import pwd` / `import grp` a nivel de módulo — módulos de la librería estándar que solo existen en Unix. No es un fallo puntual de `systemctl` o `rsync`; no se llega ni a cargar la app.

Notas:

- La carpeta `venv/` del proyecto es la copia del entorno **Linux del NAS** (`venv/bin`, `venv/lib`, creada con `/usr/bin/python3 -m venv /etc/ix2panel/venv`) que baja la sincronización SFTP. No es un venv de Windows. Está en `.gitignore`.
- El perfil de debug **"ix2panel (Flask Dev)"** de `.vscode/launch.json` fallará por el mismo motivo si se lanza con `F5` en el PC.
- La Terminal SSH integrada (xterm.js) requiere Gunicorn+eventlet corriendo detrás — no funciona con `python ix2panel.py` directo ni con el perfil de debug. Es esperable que falle en local; probarla siempre en el NAS.

Ciclo de trabajo real:

```powershell
# 1. Editar en VS Code (auto-upload SFTP al guardar)
# 2. Reiniciar el servicio en el NAS — IMPRESCINDIBLE tras tocar .py o plantillas .html
#    (TEMPLATES_AUTO_RELOAD esta desactivado: Flask cachea las plantillas compiladas en memoria)
ssh -i C:\Users\TU_USUARIO\.ssh\ix2panel_key TU_USUARIO@IP_DEL_NAS "sudo systemctl restart ix2panel"
# 3. Ver el resultado
ssh -i C:\Users\TU_USUARIO\.ssh\ix2panel_key TU_USUARIO@IP_DEL_NAS "sudo journalctl -u ix2panel -f"
# 4. Recargar el navegador con Ctrl+F5 (recarga forzada), no F5 normal —
#    una pestaña abierta desde antes de un cambio se queda con el JS/HTML viejo en memoria
#    hasta que se recarga de verdad, aunque el servidor ya sirva la version nueva.
```

Ficheros `.css`/`.js` estáticos **no** necesitan reinicio del servicio (se sirven directos desde disco) — solo Ctrl+F5 en el navegador. Ficheros `.py` y `.html` sí necesitan `systemctl restart ix2panel`.

---

## Ficheros de configuración y su ubicación en el NAS

Todos los ficheros de estado se guardan en el NAS. Nunca están en el repositorio git.

### `/var/lib/ix2panel/` — Estado en tiempo de ejecución

| Fichero | Permisos | Contenido |
|---|---|---|
| `backup.json` | `600` | Todos los jobs de backup, estado, historial y tareas recientes |
| `backup_logs/<job_id>.log` | `644` | Log completo del último backup de cada job |
| `notifications.json` | `600` | Configuración de notificaciones Telegram |
| `power_schedule.json` | `600` | Programación de apagado automático |
| `wol_targets.json` | `600` | Targets de Wake-on-LAN (si se configuran) |

Los ficheros marcados `600` son solo legibles por root — el panel aplica este permiso automáticamente cada vez que los escribe.

### `/etc/ix2panel/` — Aplicación

| Fichero | Permisos | Contenido |
|---|---|---|
| `.panel_auth.json` | `600` | Usuario y contraseña del panel (hash scrypt/PBKDF2) |
| `.secret_key` | `600` | Clave aleatoria de 64 bytes para firmar sesiones Flask |

### `/etc/wireguard/` — WireGuard (si está activo)

| Fichero | Contenido |
|---|---|
| `panel_config.json` | Configuración del servidor WireGuard gestionada por el panel |
| `panel_peers.json` | Mapeo nombre → clave pública de los peers |

### `/etc/nginx/` — Reverse proxy HTTPS

| Fichero | Contenido |
|---|---|
| `sites-available/ix2panel` (enlazado en `sites-enabled/`) | Server blocks 80→443 redirect y proxy HTTPS hacia `127.0.0.1:8080` |
| `conf.d/websocket_upgrade.conf` | `map $http_upgrade $connection_upgrade` — cabecera `Connection` condicional, solo "upgrade" en peticiones WebSocket reales |

### `/root/.ssh/` — Claves SSH para backups y terminal

| Fichero | Contenido |
|---|---|
| `id_rsa` | Clave privada RSA (usada por el modo legacy) |
| `id_rsa.pub` | Clave pública RSA |
| `id_ed25519` | Clave privada ED25519 (preferida para backups SSH) |
| `id_ed25519.pub` | Clave pública ED25519 |

La clave pública del NAS debe estar autorizada en el PC destino (ver sección SSH más abajo).

---

## Sistema de backups

Los backups se gestionan desde la pestaña **Backups** del panel. La configuración se guarda en `/var/lib/ix2panel/backup.json`.

### Modos disponibles

| Modo | Descripción |
|---|---|
| `rsync` | rsync entre rutas locales del NAS, con soporte de mount SMB como origen |
| `ssh` | robocopy en el PC Windows via SSH — el NAS se conecta al PC y ejecuta robocopy contra una ruta UNC del NAS |
| `local` | rsync interno del NAS con `-x` (no cruza límites de filesystem) |

> **Importante — modo `ssh` con destino/origen en el NAS (ruta UNC `\\NAS\...`):** la conexión SSH del NAS al PC se autentica por **clave pública**, lo que significa que esa sesión de Windows **no tiene ningún credential de red utilizable** (el clásico problema del "doble salto"). Si el job no tiene rellenos **"Usuario NAS (SMB)"** y **"Contraseña NAS (SMB)"**, robocopy intenta escribir en la ruta UNC sin autenticar nada y falla con `ERROR 5 Acceso denegado` al crear el directorio destino — sin ningún otro síntoma. La solución es rellenar esos dos campos en el editor del job: con ellos, el panel ejecuta un `net use` explícito con esas credenciales antes de lanzar robocopy (ver `system/backups.py::_do_ssh_robocopy`).

### Editor de un backup (bloqueado hasta pulsar «Editar»)

Al abrir un job, todos sus campos (nombre, modo, IP del PC, rutas, horario…) salen **bloqueados**: solo se puede **Editar**, **Ejecutar ahora** o **Ver último log**. Al pulsar *Editar* se desbloquea y aparecen *Guardar cambios*, *Cancelar* (devuelve los valores guardados) y *Eliminar*. Cambiar de backup con cambios sin guardar pregunta antes de descartarlos. *+ Nuevo backup* entra directamente en edición. Un viewer no ve el botón *Editar*. Está hecho con un `<fieldset id="backup-fields" disabled>` en `templates/tabs/backups.html` y `setBackupEditMode()` en `static/js/tabs/backups.js`.

### Jobs configurados actualmente

#### Backup_Raiz_NAS
- **Modo:** local (rsync -x)
- **Origen:** `/` (raíz del NAS)
- **Destino:** `/data1/usuario/NAS`
- **Schedule:** semanal, 10:27
- **Exclusiones:** `/proc`, `/sys`, `/dev`, `/run`, `/tmp`, `/swapfile`

#### Backup_PCRubi_D
- **Modo:** SSH → robocopy en el PC Windows
- **Origen:** `D:\` (disco D del PC Windows)
- **Destino:** `/data1/usuario/PCRubi/Backup_Disk_D` (via SMB `\\IP_DEL_NAS\data1`)
- **Schedule:** diario, 03:00
- **Usuario SSH PC:** `TU_USUARIO` con clave ED25519 del NAS
- **Usuario/Contraseña SMB NAS:** configurados (necesario, ver nota de doble salto arriba)
- **Exclusiones dirs:** `$RECYCLE.BIN`, `System Volume Information`, `Rubbish`, `.git`
- **Exclusiones ficheros:** `pagefile.sys`, `hiberfil.sys`, `wapfile.sys`, `LogRegistre_*.txt`
- **IP del PC:** asignada por DHCP, puede cambiar — si el job falla con "PC no accesible", comprobar la IP real del PC antes de nada y actualizarla en el editor del job.

#### Portatil
- **Modo:** SSH → robocopy en el PC portátil
- **Origen:** `C:\Datos`
- **Destino:** `/data1/usuario/PCPortatil/Carpeta_Datos` (via SMB `\\IP_DEL_NAS\data1`)
- **Usuario/Contraseña SMB NAS:** configurados (mismo motivo que arriba — sin esto falla con Acceso denegado)
- **Estado:** activado

#### Data1->Data2
- **Modo:** local (rsync)
- **Copia interna** de `/data1` a `/data2` como redundancia adicional dentro del propio NAS.

### Scheduler automático

El scheduler comprueba cada 60 segundos si algún job debe ejecutarse. Lanza el backup si:
1. El job está activado
2. La hora programada ya llegó o pasó
3. No se ha ejecutado en el periodo actual (día/semana/mes), o nunca se ha ejecutado
4. Si terminó con error, reintenta cada hora

Cualquier excepción inesperada durante la ejecución queda registrada en `backup.json` como `status: err` — nunca se pierde silenciosamente.

Los logs de cada ejecución se guardan en `/var/lib/ix2panel/backup_logs/<job_id>.log`.

---

## SSH desde el NAS al PC Windows

El modo de backup SSH requiere que el NAS pueda conectarse por SSH al PC Windows para ejecutar robocopy.

### Configuración en el PC Windows

> La IP del PC la asigna el router por DHCP y puede cambiar con el tiempo — comprobar la IP real (`ipconfig`) si un job deja de conectar, en vez de asumir que sigue siendo la misma.

**1. OpenSSH Server instalado y activo:**
```powershell
# Verificar estado
Get-Service sshd

# Arrancar y configurar inicio automático
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic
```

**2. Regla de firewall habilitada:**
```powershell
# Verificar
netsh advfirewall firewall show rule name="OpenSSH SSH Server (sshd)"

# Habilitar si está desactivada
netsh advfirewall firewall set rule name="OpenSSH SSH Server (sshd)" new enable=yes
```

**3. Clave pública del NAS autorizada:**

La clave pública del NAS (`/root/.ssh/id_ed25519.pub` o `id_rsa.pub`) debe estar en:
```
C:\ProgramData\ssh\administrators_authorized_keys
```
(para usuarios del grupo Administrators, según `sshd_config`).

Para obtener la clave pública del NAS:
```bash
ssh TU_USUARIO@IP_DEL_NAS "sudo cat /root/.ssh/id_rsa.pub"
```

**4. `sshd_config` relevante** (`C:\ProgramData\ssh\sshd_config`):
```
AuthorizedKeysFile      .ssh/authorized_keys
Match Group administrators
    AuthorizedKeysFile __PROGRAMDATA__/ssh/administrators_authorized_keys
```

### Síntomas conocidos y sus causas

| Síntoma | Causa habitual |
|---|---|
| `sshd` no arranca | Host keys con permisos incorrectos o regla de firewall deshabilitada |
| `sshd` arranca pero el NAS no conecta | Regla de firewall deshabilitada (`enable=no`) |
| Backup falla con "PC no accesible" | PC apagado, sshd parado, regla firewall deshabilitada, o la IP del PC cambió (DHCP) |
| Backup falla con "Acceso denegado" creando el directorio destino | Falta "Usuario/Contraseña SMB NAS" en el job — ver [Sistema de backups](#sistema-de-backups) |

> **Incidencia registrada (mayo 2026):** Tras regenerar las host keys de Windows, la regla de firewall `"OpenSSH SSH Server (sshd)"` quedó deshabilitada. El servicio arrancaba pero el NAS no podía alcanzar el puerto 22. Solución: `netsh advfirewall firewall set rule name="OpenSSH SSH Server (sshd)" new enable=yes` (requiere admin).

### Reparar OpenSSH en Windows si no arranca

Si `sshd` falla con _"no hostkeys available"_ (permisos corruptos en las host keys):

```powershell
# En PowerShell como Administrador:

# Opción 1: reparar permisos
$keys = Get-Item "C:\ProgramData\ssh\ssh_host_*_key"
foreach ($key in $keys) {
    icacls $key /inheritance:r /grant "SYSTEM:(F)" /grant "Administrators:(F)"
}
Start-Service sshd

# Opción 2: regenerar claves de host desde cero
Remove-Item "C:\ProgramData\ssh\ssh_host_*" -Force
& "C:\Windows\System32\OpenSSH\ssh-keygen.exe" -A -f "C:\ProgramData\ssh"
Start-Service sshd
```

> **Nota:** Regenerar las host keys no afecta a las `authorized_keys` ni a la configuración de backups.

---

## Notificaciones Telegram

Configuradas en `/var/lib/ix2panel/notifications.json`. Se gestionan desde la pestaña **Sistema → Notificaciones** del panel.

### Eventos disponibles

| Evento | Descripción |
|---|---|
| `backup_ok` | Backup completado con éxito |
| `backup_err` | Backup terminado con error |
| `backup_stale` | Backup no se ha ejecutado en el tiempo esperado |
| `nas_start` | El NAS ha arrancado |
| `service_down` | Un servicio monitorizado ha caído |
| `disk_full` | Disco por encima del umbral de uso |
| `login_ok` | Acceso correcto al panel |
| `login_fail` | Intento de acceso fallido |
| `temp_high` | Temperatura CPU alta |
| `ram_low` | RAM disponible baja |
| `ip_change` | La IP pública ha cambiado |
| `wg_peer` | Conexión/desconexión de peer WireGuard |

### Configurar el bot de Telegram

1. Crear un bot con [@BotFather](https://t.me/BotFather) y obtener el token
2. Obtener el `chat_id` iniciando conversación con el bot y consultando `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. Introducir token y chat_id en el panel (pestaña Sistema → Notificaciones)

---

## WireGuard VPN

Si está configurado, el servidor WireGuard corre en el NAS. La IP de la interfaz VPN es `10.0.0.1/24` (interfaz `wg0`).

La configuración gestionada por el panel se guarda en:
- `/etc/wireguard/panel_config.json` — parámetros del servidor
- `/etc/wireguard/panel_peers.json` — mapeo nombre → pubkey de peers

El panel permite añadir peers y genera un QR code para configurar clientes móviles directamente. Es la forma recomendada de dar acceso a shares SMB a alguien fuera de la LAN (ver [Compartidas SMB](#compartidas-smb)) — mucho más seguro que abrir el puerto 445 a internet.

> **Ojo con el enrutamiento en clientes Windows:** si el cliente WireGuard tiene como `AllowedIPs` el rango de la LAN de casa (p.ej. `192.168.1.0/24`) y el PC con el cliente está *también* físicamente conectado a esa misma LAN (WiFi de casa), Windows puede acabar enrutando el tráfico local a través del túnel en vez de directo, rompiendo el acceso a la LAN mientras el túnel esté activo. Si esto pasa, comprobar la tabla de rutas (`Get-NetRoute`) — la ruta del túnel suele tener métrica más baja que la de la interfaz física y gana por defecto.

---

## Acceso remoto por HTTPS (nginx + DDNS)

El panel es accesible desde fuera de la LAN vía **`https://tu-dominio.ddns.net`**, con nginx como proxy inverso hacia `127.0.0.1:8080` y certificado Let's Encrypt.

Configuración: `/etc/nginx/sites-available/ix2panel` (enlazado en `sites-enabled/`):

```nginx
server {
    listen 80;
    server_name tu-dominio.ddns.net;
    return 301 https://tu-dominio.ddns.net$request_uri;
}

server {
    listen 443 ssl;
    server_name tu-dominio.ddns.net;

    ssl_certificate /etc/letsencrypt/live/tu-dominio.ddns.net/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/tu-dominio.ddns.net/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header X-Forwarded-Port $server_port;
    }
}
```

`$connection_upgrade` viene de un `map` en `/etc/nginx/conf.d/websocket_upgrade.conf` (necesario para que el include se aplique dentro del contexto `http{}`):

```nginx
map $http_upgrade $connection_upgrade {
    default upgrade;
    "" close;
}
```

Esto hace que la cabecera `Connection: upgrade` solo se envíe en peticiones que de verdad piden cambio de protocolo (el WebSocket de la Terminal) — no en cada petición HTTP normal.

> En la práctica, `https://tu-dominio.ddns.net` funciona bien tanto desde dentro como desde fuera de la LAN — es la vía recomendada siempre. El acceso directo `http://IP_DEL_NAS:8080` **no es fiable**: se ha observado repetidamente que deja de responder desde un cliente concreto tras muchas conexiones seguidas a ese puerto (posible límite/rate-limit no identificado), mientras SSH y el propio servidor van bien. No usar esa vía para el día a día.

---

## Terminal SSH integrada

Pestaña **Terminal** (solo visible con rol `admin`), abre una sesión SSH real contra `localhost:22` del propio NAS usando **el usuario Linux real** (no el usuario del panel), vía WebSocket en `/ws/ssh` + `xterm.js` en el navegador.

### Requisitos imprescindibles

- **Gunicorn con worker `eventlet`**, no el servidor de desarrollo de Flask (`app.run()`). El servidor de desarrollo no soporta correctamente la "toma de control" (hijack) del socket que necesita un WebSocket de larga duración — con él, la conexión se corrompe (`Invalid frame header` en el navegador) en cuanto se envía el primer frame.
- **`eventlet.monkey_patch()` como primera línea de `ix2panel.py`**, antes de cualquier otro import. Sin esto, `simple_websocket` (que usa `threading.Thread` real para leer del socket) choca con los sockets parcheados por eventlet y el worker crashea con `eventlet.hubs.IOClosed`.
- El worker `gthread` de Gunicorn **no sirve** para esto — parece funcionar en el caso trivial (mensaje de error inmediato) pero corrompe la conexión en silencio (sin traceback) en cuanto hay streaming real de datos. Solo `eventlet`/`gevent` soportan correctamente el hijack de WebSocket bajo Gunicorn.
- **`SOCK_SERVER_OPTIONS = {"ping_interval": 25}`** (en `ix2panel.py`, justo tras crear `Sock(app)`). Sin esto, `simple_websocket` no manda pings ni detecta nunca una conexión muerta por su cuenta: si el navegador desaparece sin cerrar limpio el WebSocket (portátil suspendido, wifi caída, pestaña matada a la fuerza...), tanto su hilo interno como `ws.receive()` en `ws_ssh()` se quedan bloqueados **para siempre** — Gunicorn corre con `--timeout 0` a propósito (ver más abajo), así que nada los mata. Cada terminal huérfana así deja vivos 2 hilos + una conexión SSH real (con su `sshd-session` en el NAS) indefinidamente — en un NAS de 234MB de RAM, unas cuantas de estas bastan para acabar en `Worker was sent SIGKILL! Perhaps out of memory?`. Con `ping_interval`, `simple_websocket` detecta el pong ausente y cierra la conexión sola, lo que desbloquea `ws.receive()` y deja que el `finally` de `ws_ssh()` limpie todo con normalidad.

### xterm.js — carga perezosa

Los ficheros reales de xterm.js (~283KB entre JS+CSS+addon) están en `static/vendor/xterm/` pero **no se cargan en cada visita al panel** — `terminal.js` los inyecta dinámicamente (`_loadXtermAssets()`) solo la primera vez que se abre la pestaña Terminal, y empieza a precargarlos en cuanto se hace clic en la pestaña (antes de pulsar "Conectar"), para no penalizar la carga inicial del resto del panel.

### Timeouts SSH

Por el hardware ARM lento (ver [Hardware del NAS](#hardware-del-nas)), los timeouts de paramiko para el login (`auth_timeout`, `banner_timeout`) están en **20s** en vez de los 5s por defecto — con 5s, el hash de contraseña PAM no siempre da tiempo y salta "Authentication timeout" aunque las credenciales sean correctas.

### Autocompletado del navegador

Los campos de usuario/contraseña de la Terminal llevan un campo señuelo oculto + `readonly` hasta el primer foco, para evitar que Chrome autorrellene con credenciales de otros formularios del panel (ignora `autocomplete="off"` en campos que "parecen" login).

### Credenciales del login del panel vs credenciales de la Terminal

El login del panel cachea temporalmente usuario/contraseña (`_CRED_CACHE`, TTL 15 min) **solo cuando `_PANEL_AUTH_MODE` es `"ssh"`** — es decir, cuando el panel se autentica contra una cuenta Linux real. En modo `"panel"` (usuario propio del panel, sin relación con las cuentas Linux del NAS), esas credenciales casi seguro no son una cuenta Linux válida, así que **no** se cachean: la Terminal siempre pide usuario/contraseña Linux reales mediante su propio formulario, aunque se haya iniciado sesión en el panel con otra cuenta.

---

## Compartidas SMB

Pestaña **Compartidas**. Permite crear shares SMB apuntando a cualquier ruta bajo `/data1`, `/data2`, `/mnt`, `/media`, `/run/media`, `/home` o `/srv` — **incluidas subcarpetas de un share ya existente**, con usuario/grupo distintos (p.ej. compartir `/data1/Carpeta2` con un usuario `usuario2` aparte, dentro de un `/data1` que ya está compartido con `usuario1`).

### Permiso de tránsito en carpetas padre

Cuando el share vive dentro de otra carpeta con permisos más restrictivos, no basta con darle permisos a la carpeta del share en sí — el usuario también necesita poder **atravesar** cada carpeta antecesora hasta llegar ahí (bit de ejecución `x`), aunque no pueda ver su contenido. El panel gestiona esto automáticamente con ACL POSIX (`setfacl -m u:usuario:--x /ruta/antecesora`) al crear o editar un share — **requiere el paquete `acl` instalado** (`apt install acl`). Sin él, el share se crea igualmente pero el panel avisa con un mensaje de aviso de permisos, y el usuario no podrá acceder de verdad hasta instalarlo y volver a guardar el share.

Esta ACL es solo de tránsito (`--x`, sin lectura) — no da acceso al contenido de las carpetas intermedias, solo permite "pasar por dentro" para llegar al share final.

### Varios usuarios individuales en un mismo share

Si un share tiene **más de un usuario individual** en el campo "Usuarios" (sin grupo), el panel actualiza a quién deja entrar Samba pero **no cambia el propietario real de la carpeta en Linux** — el código de permisos solo sabe aplicar `chown` cuando hay un único usuario, o cuando se especifica un grupo. Con dos o más usuarios sueltos, el dueño real de la carpeta se queda como estaba, y solo el primer usuario (el dueño) tendrá acceso de verdad a nivel de sistema de archivos, aunque Samba deje "intentarlo" a los demás.

**Para compartir una carpeta entre varias personas concretas, usar un grupo:**
1. Crear un grupo en la pestaña **Grupos** (p.ej. `familia`)
2. Añadir a ese grupo a todos los usuarios que deban tener acceso
3. En el share, dejar el campo **"Grupos"** con ese nombre (el campo "Usuarios" puede quedar vacío)

Con grupo, el panel aplica `chown :grupo` + modo `2770` (setgid, para que los ficheros nuevos hereden el grupo) — acceso real para todos los miembros.

### Árbol de carpetas

El selector de ruta al crear/editar un share usa un árbol de carga perezosa (`PathTreeUI.createLazyBrowser` en `path_tree.js`) — se puede desplegar a cualquier profundidad haciendo clic, no solo un par de niveles fijos como en versiones anteriores.

---

## Roles y autenticación

El panel tiene tres roles:

| Rol | Permisos |
|---|---|
| `viewer` | Solo lectura del estado general: dashboard, discos, procesos, red, servicios y estado de los backups. **No** ve la pestaña Logs (ni `/api/logs`), ni el árbol de carpetas de Discos (`/api/fs/list`), ni usuarios, grupos, compartidas, VPN, montajes, Wake-on-LAN, fail2ban ni el DDNS (esas rutas exigen `operator` y sus secciones se ocultan). De `/api/backups` recibe los jobs **sin** su configuración (`smb`, `ssh`, `local`: hosts, usuarios y contraseñas) |
| `operator` | Lo anterior + logs, ejecutar backups, reiniciar servicios |
| `admin` | Acceso completo — gestión de usuarios, configuración, apagado, Terminal |

Qué exige `admin` (y no basta con `operator`), porque cambia la seguridad o el estado del sistema:

- Parar un servicio (`/api/services/<n>/stop`) y activar/desactivar el firewall (`/api/services/firewall/enable|disable`). Reiniciar un servicio sí lo puede hacer un `operator`.
- Ejecutar actualizaciones (`/api/updates/run`).
- Todo lo que modifica usuarios, grupos, compartidas, WireGuard, montajes, discos, energía y SMART.

**Contraseñas de los jobs de backup:** solo un `admin` recibe las contraseñas SMB, `nas_pass` y la config WireGuard de un job (`/api/backups*`). Un `operator` las recibe vacías, con un indicador de "guardada" (`password_set`, `nas_pass_set`, ...); si guarda el job con ese campo vacío, se conserva la anterior (por tanto un operator puede cambiarla, pero no borrarla). Un `viewer` recibe los jobs sin su configuración (`smb`, `ssh`, `local`).

### Validación de entradas (resumen)

Ninguna ruta usa `shell=True`: los comandos van como listas y las contraseñas por stdin o por ficheros `0600`. Además se validan en el servidor:

- **Discos** (`system/disks.py`): `mount_partition` solo acepta dispositivos reales (`/dev/sdb1`, `nvme0n1p2`...) y el punto de montaje, tras `realpath`, debe ser un subdirectorio de `/mnt` o `/media`. Igual al desmontar.
- **Montajes SMB** (`system/mounts.py`, `system/backups.py`): credenciales en un fichero `0600` en `/run` que se borra al terminar (nunca en la línea de comandos, donde `ps` las vería y una coma inyectaría opciones de `mount`).
- **Backups rsync**: un origen o destino que empiece por `-` se rechaza (rsync lo leería como opción).
- **Apagado programado** (`system/power.py`): hora `HH:MM` y días `all`/`workdays`. El apagado lo hace el propio panel (el hilo del planificador de backups lo comprueba cada minuto), no el cron de root: si a esa hora hay un backup en marcha, espera a que termine (máx. 3 h) y avisa por Telegram; si pasa el tope, se omite esa noche. No apaga si el panel arrancó después de la hora (NAS recién encendido). Al arrancar, el panel borra la línea antigua `ix2panel-shutdown` del cron de root. Si el panel está caído a la hora programada, esa noche no se apaga.
- **SMART** (`system/smart.py`): solo discos físicos (`/dev/sda`, `/dev/nvme0n1`...), nunca particiones ni rutas raras.
- **Fail2ban** (`system/fail2ban.py`): el jail por patrón y la IP con `ipaddress`.
- **Grupos** (`system/groups.py`): el usuario debe tener nombre válido y existir antes de llamar a `usermod`/`gpasswd`.
- **Sin scripts inline (preparado para CSP)**: las plantillas no llevan `<script>` con código ni manejadores `onclick=`; todo está en `static/js/` (`role.js`, `login.js`, `tabs/updates.js`, y el botón de Wake-on-LAN en `tabs/red.js`). Así se puede mandar desde nginx una cabecera `Content-Security-Policy` con `script-src 'self'` (los estilos inline siguen permitidos). Nuevo código: no añadir `<script>` inline ni `onclick=`.
- **Frontend (XSS)**: el servidor no filtra `<`, `>` ni comillas en nombres de carpetas ni en las líneas de log. Por eso todo dato que el JS mete en un `innerHTML` pasa por `escHtml()` (definida en `core.js`); el texto plano va con `textContent`.

Las credenciales se guardan en `/etc/ix2panel/.panel_auth.json` con hash scrypt/PBKDF2. Se configuran la primera vez que se accede al panel o desde la sección de autenticación.

### Usuarios del panel (Sistema → "Usuarios del panel", solo admin)

Un admin puede listar, crear, borrar y cambiar el rol de las cuentas del panel (rutas `/api/panel-users`, todas `admin`). El fichero `.panel_auth.json` queda con sangría (un campo por línea) y permisos `600`.

- Usuario: 1-32 caracteres (`A-Z a-z 0-9 . _ -`), sin repetir aunque cambie la capitalización. Contraseña: mín. 10, con mayúsculas, minúsculas y números.
- No se puede cambiar el propio rol ni borrarse a uno mismo, ni quitar o borrar al último admin.
- **El rol se relee del fichero en cada petición.** Un cambio de rol o un borrado surte efecto al momento, también en sesiones ya abiertas (una cuenta borrada pierde la sesión).
- **Una cuenta sin campo `role` es `viewer`**, nunca admin (antes se cargaba como admin).
- El formulario "Credenciales del panel" cambia solo la cuenta con la que has entrado (usuario y contraseña). No permite renombrarla a un nombre ya existente.
- Un fallo de permisos (403) en un botón de servicios o firewall muestra el aviso "No tienes permiso para hacer esto con tu rol."
- El botón **Reiniciar panel** (Sistema) exige `admin` y solo se muestra a los admin. Si el servidor rechaza la petición (4xx) no se muestra la pantalla de espera: solo esa pantalla sale cuando la conexión se corta de verdad por el reinicio.
- **Apagar NAS, Reiniciar NAS y Reiniciar panel se niegan si hay un backup en marcha** (`/api/system/shutdown`, `reboot` y `restart-panel`: devuelven `{"error": ...}` con el nombre del backup y el navegador lo muestra en una ventana). La orden se anula, no se pospone: los jobs corren dentro del proceso del panel y se cortarían. El apagado **programado** es distinto: espera al backup y apaga al terminar. No protege `sudo systemctl restart ix2panel` ni `reboot`/`poweroff` por SSH.
- Si todo falla y no hay ningún admin utilizable, se recupera editando `.panel_auth.json` a mano como root (el `pass_hash` se genera con `werkzeug.security.generate_password_hash`).

> **Cuidado con "Eliminar usuario" en la pestaña Usuarios:** borra la cuenta Linux por completo (`userdel`), incluida su carpeta personal `/home/<usuario>` si estaba marcada la opción de borrar home — no hay forma de deshacerlo ni de recuperar la contraseña. Si el usuario tenía carpetas compartidas (`system/shares.py`), esas carpetas **no se borran** (siguen en disco con el UID numérico antiguo, ya sin nombre asociado) — para recuperar el acceso, crear de nuevo el usuario (normalmente recupera el mismo UID si es el más bajo disponible) y volver a guardar el share para que el panel reaplique el propietario correcto.

---

## Servicio systemd (Gunicorn)

Fichero: `/etc/systemd/system/ix2panel.service`

```ini
[Unit]
Description=Servicio Panel de Control ix2-NG
After=network.target

[Service]
User=root
WorkingDirectory=/etc/ix2panel
ExecStart=/etc/ix2panel/venv/bin/gunicorn -k eventlet -w 1 --timeout 0 --bind 0.0.0.0:8080 ix2panel:app
StandardOutput=null
StandardError=journal
Restart=always

[Install]
WantedBy=multi-user.target
```

Notas sobre los flags:
- **`-k eventlet`**: obligatorio para que la Terminal SSH (WebSocket) funcione — ver [Terminal SSH integrada](#terminal-ssh-integrada). El worker `sync`/`gthread` por defecto de Gunicorn no soporta el hijack de socket que necesita.
- **`-w 1`**: un único proceso worker. El panel guarda estado en memoria (progreso de backups en vivo, credenciales de sesión SSH...) — con más de un worker, cada uno tendría su propia copia y el estado se desincronizaría entre peticiones. En un NAS doméstico de un único usuario/familia, un worker es más que suficiente.
- **`--timeout 0`**: desactiva el timeout de Gunicorn para conexiones inactivas — necesario porque la Terminal puede quedarse abierta sin actividad (usuario pensando en el prompt) más tiempo del timeout por defecto (30s), y Gunicorn la mataría de otro modo.

> El servidor de desarrollo de Flask (`python ix2panel.py` directo) sigue funcionando para pruebas puntuales fuera de la Terminal — pero **nunca** para producción ni para probar la pestaña Terminal, que requiere Gunicorn+eventlet sí o sí.

### Comandos útiles

```bash
# Estado
systemctl status ix2panel

# Ver logs en tiempo real
journalctl -u ix2panel -f

# Reiniciar tras actualizar código
systemctl restart ix2panel

# Habilitar arranque automático
systemctl enable ix2panel
```

---

## Consola serie del NAS (FTDI FT232RL)

El IX2-ng tiene una consola serie (la línea de arranque lleva `console=ttyS0,115200`). Sirve para ver el arranque completo, entrar al prompt del U-Boot y recuperar el NAS si un kernel nuevo no arranca. Guía de referencia: <https://minaret.biz/tips/ix2/index.html>. Las fotos del conector `CN4` y su cableado están en esa guía (no se incluyen aquí). En [`Imatges/`](Imatges/) están las del adaptador (`adaptador_ftdi_etiquetas.jpeg`, con las etiquetas legibles; `adaptador_ftdi_cables.jpg`, con **nuestros cables ya enchufados**: negro en `GND`, verde en `TX` y blanco en `RX`, el resto de pines al aire).

- **Adaptador:** módulo rojo FT232RL. **El puente de voltaje debe ir en 3,3 V** (cubre el pin central y el de la derecha, el que pone `3.3V`). Con el módulo en el USB, entre `VCC` y `GND` deben medirse unos 3,3 V.
- **Fila de 6 pines del adaptador**, de izquierda a derecha con el chip hacia arriba: `DTR`, `Rx`, `Tx`, `VCC`, `CTS`, `GND`. Se usan solo `Rx`, `Tx` y `GND`. **`VCC` no se conecta** (el NAS se alimenta solo).
- **Conector del NAS:** `CN4`, 4 pines en fila, junto a `JP1`, en la esquina inferior derecha de la placa, con los pines hacia el frente de la caja. Según la guía, con la placa como en la foto y contando de arriba abajo: **pin 1 = 3,3 V (no usar), pin 2 = TX del NAS, pin 3 = GND, pin 4 = RX del NAS**. Los cables de la foto (amarillo, negro, naranja) son los de su cable FTDI. **Pendiente de comprobar con multímetro en nuestra placa** (3,3 V en el pin 1, continuidad con la carcasa en el pin 3) antes de conectar.
- **Conexión** (van cruzados): pin 2 del NAS (TX) al pin `Rx` del adaptador (el 2º de la fila); pin 3 (GND) al `GND` del adaptador (el 6º); pin 4 del NAS (RX) al pin `Tx` del adaptador (el 3º). El pin 1 y el `VCC` del adaptador, sin conectar.
- **Nuestros cables** (por la foto de enero, a confirmar mirando `CN4`): pin 2 del NAS = **blanco** (va al `RX` del adaptador), pin 3 = **negro** (GND), pin 4 = **verde** (va al `TX` del adaptador); pin 1 sin cable. Si estuvieran verde y blanco al revés, basta con cambiarlos de sitio en el adaptador.
- **Estado: consola verificada el 26 oct 2026**: con los cables de la foto de enero se ve el apagado del NAS en directo (apagado a mano por el usuario para comprobarlo; 115200 8N1). La consola de Linux (`serial-getty@ttyS0`) está activa, así que también sale el login sin reiniciar.
- **U-Boot:** se interrumpe el arranque pulsando ENTER; `printenv` muestra su entorno.
- **Parámetros del puerto:** 115200, 8 bits, sin paridad, 1 de parada (8N1), sin control de flujo (PuTTY o Tera Term en el puerto COM del FTDI).
- **Con el NAS apagado y desenchufado** para abrirlo y tocar cables.

## Kernel del NAS

Kernel de bodhi (Kirkwood), **no gestionado por apt**: la pestaña *Actualizar* del panel actualiza los paquetes de Debian, y el kernel solo se muestra como "Firmware". Desde el 26 oct 2026 el NAS arranca el **7.2.11-kirkwood-tld-1** (antes 5.18.7).

- **Arranque:** el U-Boot (en la flash SPI, no se toca) carga de la partición `/boot` (`sdb1`, ext2) los ficheros `uImage` y `uInitrd`, con `root=/dev/sdb2`. Es un U-Boot antiguo sin FDT: el DTB (`kirkwood-lenovo-ix2-ng.dtb`) va pegado al final del `zImage` (método "4b" del hilo de bodhi) y el `uInitrd` debe pesar **menos de 11 MB** (initramfs con `MODULES=dep` y gzip).
- **Copias en `/boot`:** `uImage.old` y `uInitrd.old` son los del kernel 5.18.7; los módulos de ambos están en `/lib/modules/`.
- **Si no arranca:** por la consola serie, parar U-Boot con ENTER y repetir el `ide_boot` a mano con `/uImage.old` y `/uInitrd.old` (`setenv bootargs console=ttyS0,115200 root=/dev/sdb2 rw rootwait`, solo en RAM). **Nunca `saveenv`**: el entorno vive en la flash y podría dejar el NAS sin arrancar.
- **Desde el panel (Sistema → Kernel del NAS, solo admin, sin backups en marcha):** se sube el `.tar.bz2` de bodhi con el sha256 que publica en su hilo; el panel comprueba el hash, instala los módulos y el `config`, y deja `/boot/uImage.new` y `/boot/uInitrd.new` **sin tocar** los actuales (el `uInitrd` debe quedar bajo ~10,9 MB, si no, se niega). Después se prueba el arranque a mano por la consola serie, y solo entonces se pulsa *Activar* (el kernel actual pasa a `.old`). Hace falta que nginx admita la subida: `location = /api/kernel/upload` con `client_max_body_size 128m` y `proxy_request_buffering off`.
- **Kernel nuevo (a mano):** no usar `dpkg -i` del `.deb` (sus scripts tocan `/boot`); extraerlo con `dpkg-deb -x` y copiar solo `lib/modules/<versión>` a `/lib/modules`. Copiar también `config-<versión>` a `/boot` (sin él falla el hook `kmod` de initramfs-tools en este armel). Probar primero con `uImage.new`/`uInitrd.new` desde el prompt de U-Boot antes de renombrar.

## Módulos del sistema

El panel expone una API REST en `/api/*`. Cada módulo en `system/` implementa las funciones que usa la API.

| Módulo | API | Descripción |
|---|---|---|
| `disks.py` | `/api/disks/*` | Particiones, uso, SMART, montar/desmontar |
| `backups.py` | `/api/backups/*` | Jobs, scheduler, rsync, SSH/robocopy, logs |
| `services.py` | `/api/services/*` | systemctl status/start/stop/restart |
| `users.py` | `/api/users/*` | CRUD usuarios Linux + Samba |
| `groups.py` | `/api/groups/*` | CRUD grupos Linux |
| `shares.py` | `/api/shares/*` | Compartidas Samba (smb.conf), incluye ACL de tránsito para subcarpetas |
| `wireguard.py` | `/api/wg/*` | Servidor WireGuard, peers, QR |
| `network.py` | `/api/system` | Info red, DDNS, estadísticas |
| `notifications.py` | `/api/notifications/*` | Config Telegram, test, monitor |
| `alerts.py` | `/api/system` | Alertas automáticas del sistema |
| `logs.py` | `/api/logs` | journalctl con filtros |
| `power.py` | `/api/power/*` | Apagado, reboot, schedule |
| `updates.py` | `/api/updates/*` | apt list/upgrade |
| `kernel.py` | `/api/kernel/*` | Subir el paquete de bodhi, preparar `uImage.new`/`uInitrd.new`, activar o descartar (solo admin) |
| `wol.py` | `/api/wol/*` | Wake-on-LAN — alta, edición y borrado de targets, envío del magic packet, apagado remoto por SSH |
| `fail2ban.py` | `/api/fail2ban/*` | Jails, IPs baneadas, unban |
| `smart.py` | `/api/disks/smart` | Datos SMART via smartctl |
| `mounts.py` | `/api/mounts/*` | Montar/desmontar SMB/NFS |
| `fs.py` | `/api/fs/*` | Explorador de ficheros / listado de directorios para los árboles de rutas |

El WebSocket `/ws/ssh` proporciona la terminal SSH integrada vía xterm.js + paramiko (ver [Terminal SSH integrada](#terminal-ssh-integrada)).

`/api/system/ping` (GET) es un endpoint deliberadamente barato — sin el `psutil` pesado de `/api/system` — que devuelve `{"ok": true, "pid": <pid del proceso>}`. Lo usa el overlay de "Reiniciar panel" (pestaña Sistema) para sondear cada 1-2s sin coste durante el reinicio, y sobre todo para comparar el PID antes/después: `restart_panel_service()` hace `sleep 2; systemctl restart ix2panel` en segundo plano tras responder al click, así que el proceso viejo sigue respondiendo un instante — si el overlay se fiara de cualquier 200 OK, el primer sondeo (también a los 2s) casi siempre acertaría contra el viejo, daría el reinicio por bueno antes de tiempo y el `reload()` automático caería justo en la ventana en que el viejo ya murió y el nuevo aún no escucha (502 de nginx en blanco). Comparando el PID, el overlay solo recarga cuando responde el proceso **nuevo**.

---

## Solución de problemas

### El panel no arranca

```bash
journalctl -u ix2panel -n 50 --no-pager
# Comprobar que el venv tiene las dependencias:
/etc/ix2panel/venv/bin/pip list
```

Si el log muestra `ImportError: cannot import name 'X' from 'system.Y'` justo al arrancar, casi siempre es que el SFTP-on-save de VS Code subió `ix2panel.py` (que ya importa `X`) pero no `system/Y.py` (que aún no lo define) — comparar fechas:

```bash
ls -la --time-style=full-iso /etc/ix2panel/ix2panel.py /etc/ix2panel/system/Y.py
```

Si `system/Y.py` tiene fecha más antigua de lo esperado, volver a subirlo a mano (`scp`) y `systemctl restart ix2panel`. Mientras el import falle, systemd reinicia el servicio en bucle y nginx da 502 en cada intento — **no se autorrepara subiendo el fichero que falta, hay que reiniciar el servicio otra vez tras subirlo**.

### "Reiniciar panel" (pestaña Sistema) se queda en 502 en blanco de nginx

Puede pasar por dos motivos distintos:

1. **El proceso realmente no ha levantado** (ver el punto anterior — mirar `journalctl -u ix2panel -n 50` para el motivo real).
2. **Falso 502 por una carrera ya corregida**: el overlay de progreso comprobaba `/api/system` y daba por bueno cualquier `200 OK`, incluido el del proceso *viejo* que sigue vivo un instante tras el click (`restart_panel_service()` espera 2s antes de reiniciar, y el primer sondeo del navegador también es a los 2s). El `reload()` automático entonces caía justo en la ventana muerta entre el viejo y el nuevo proceso. Arreglado comparando el PID antes/después vía `/api/system/ping` — ver la nota en [Módulos del sistema](#módulos-del-sistema).

### Un backup falla con "PC no accesible"

1. Comprobar que el PC está encendido
2. Verificar que el servicio OpenSSH está activo en Windows: `Get-Service sshd`
3. Verificar que la regla de firewall está habilitada: `netsh advfirewall firewall show rule name="OpenSSH SSH Server (sshd)"`
4. Comprobar que la IP configurada en el job sigue siendo la real del PC (DHCP puede haberla cambiado)
   - **Si el PC está fuera de casa** (portátil con WireGuard), la IP de la LAN (`192.168.1.x`) no existe para el NAS: usar la IP del túnel del PC (`10.0.0.x`, la que sale en la pestaña VPN) y tener el túnel activado en el PC. Con la IP de LAN el job fallará cada hora con "PC no accesible".
   - El botón "Generar config WG" del editor del job **crea un peer nuevo en el NAS** cada vez que se pulsa. Si ya tienes el túnel del PC, no hace falta; si se pulsó sin querer, borrar ese peer en la pestaña VPN.
5. Probar conectividad desde el NAS: `ssh -i /root/.ssh/id_rsa TU_USUARIO@<IP_DEL_PC> "echo ok"`

### Un backup falla con "Acceso denegado" creando el directorio destino

Falta rellenar **"Usuario NAS (SMB)"** y **"Contraseña NAS (SMB)"** en el editor del job — ver la nota de "doble salto" en [Sistema de backups](#sistema-de-backups).

### OpenSSH en Windows no arranca ("no hostkeys available")

Ver sección [Reparar OpenSSH en Windows](#reparar-openssh-en-windows-si-no-arranca).

### El backup SSH funcionó antes pero ahora falla la autenticación

Las host keys del PC se regeneraron. El NAS recuerda la huella SSH de cada PC la primera vez que se conecta (`/var/lib/ix2panel/known_hosts`, `0600`) y **rechaza** el backup si cambia, con un mensaje claro. Para que acepte la nueva: `sudo ssh-keygen -R <ip_del_pc> -f /var/lib/ix2panel/known_hosts`. (La misma huella guardada por otro hilo se acepta, porque un job abre varias conexiones a la vez; las dos conexiones a `localhost` de `ix2panel.py` siguen con `AutoAddPolicy`: es el propio NAS.) Si falla la autenticación de clave pública, verificar que `/root/.ssh/id_rsa.pub` del NAS sigue en `C:\ProgramData\ssh\administrators_authorized_keys` del PC.

### La Terminal da "Error de conexión WebSocket" / "Invalid frame header" en la consola del navegador

El servicio no está corriendo con Gunicorn+eventlet (o le falta el `eventlet.monkey_patch()` al principio de `ix2panel.py`). Comprobar:
```bash
systemctl status ix2panel   # deberia mostrar "gunicorn -k eventlet..." en el ExecStart
journalctl -u ix2panel -n 40 --no-pager   # buscar tracebacks de eventlet/simple_websocket
```
Ver [Servicio systemd (Gunicorn)](#servicio-systemd-gunicorn) y [Terminal SSH integrada](#terminal-ssh-integrada).

### La Terminal da "Authentication timeout"

Hardware ARM lento, el hash de contraseña PAM tarda más de los 5s por defecto de paramiko. Ya está subido a 20s en `ix2panel.py` (`_ssh_check_local` y `ws_ssh`) — si sigue pasando, subir aún más esos valores.

### Un share da aviso de permisos al crearlo ("No se pudo dar tránsito...")

Falta el paquete `acl` (`sudo apt install -y acl`). No bloquea la creación del share, pero el usuario no podrá acceder de verdad hasta instalarlo y volver a guardar el share. Ver [Compartidas SMB](#compartidas-smb).

### Alguien puede ver una carpeta pero no entrar (Acceso denegado) aunque esté en la lista de "Usuarios" del share

Si el share tiene **más de un usuario individual** sin grupo, el panel no sabe a quién hacer dueño de la carpeta y no toca el propietario real. Solución: usar un grupo en vez de varios usuarios sueltos — ver [Compartidas SMB](#compartidas-smb).

### Una pestaña (Grupos, Backups, Logs, Sistema...) muestra datos desactualizados aunque el cambio ya se aplicó

Comprobar primero con Ctrl+F5 si es caché del navegador (ver nota en [Desarrollo en Windows](#desarrollo-en-windows)). Las pestañas Estado, Discos, Compartidas, Usuarios, Grupos, Red, Servicios, Backups, VPN, Logs y Sistema ya refrescan sus datos cada vez que se hace clic en ellas (no solo en la carga inicial del panel).

### El panel/NAS va lento en general

Antes de sospechar de un fallo, comprobar carga real del sistema — en este hardware (ver [Hardware del NAS](#hardware-del-nas)) es fácil saturarlo con transferencias grandes o muchas conexiones simultáneas:
```bash
uptime                 # load average alto (>2) en este hardware es carga real, no un bug
free -h                # si esta usando mucho swap, es presion de memoria genuina
ps aux --sort=-%cpu | head -10
```

### Ver el log del último backup

```bash
cat /var/lib/ix2panel/backup_logs/<job_id>.log
```
Los `job_id` se encuentran en `/var/lib/ix2panel/backup.json`.

### Actualizar el código en el NAS

Con VS Code + extensión SFTP (sincronización automática al guardar) o manualmente:
```bash
# Desde Windows
scp -i C:\Users\TU_USUARIO\.ssh\ix2panel_key system/backups.py TU_USUARIO@IP_DEL_NAS:/etc/ix2panel/system/
ssh -i C:\Users\TU_USUARIO\.ssh\ix2panel_key TU_USUARIO@IP_DEL_NAS "sudo systemctl restart ix2panel"
```

> **Nota:** Los ficheros `updates.py`, `power.py` y `firmware.py` solo existen completos en el NAS (`/etc/ix2panel/system/`). Si se recupera el proyecto desde git o desde una copia local, hay que descargarlos del NAS:
> ```bash
> scp -i C:\Users\TU_USUARIO\.ssh\ix2panel_key TU_USUARIO@IP_DEL_NAS:/etc/ix2panel/system/updates.py system/
> scp -i C:\Users\TU_USUARIO\.ssh\ix2panel_key TU_USUARIO@IP_DEL_NAS:/etc/ix2panel/system/power.py system/
> scp -i C:\Users\TU_USUARIO\.ssh\ix2panel_key TU_USUARIO@IP_DEL_NAS:/etc/ix2panel/system/firmware.py system/
> ```
---

## Contacto y colaboración

Es un proyecto personal, pero si quieres decirme algo, la vía es GitHub:

- **Fallos, dudas o sugerencias:** abre un [issue](../../issues) en este repositorio. Indica la versión del NAS y del kernel, qué esperabas que pasara y qué pasó, y el log relevante (`journalctl -u ix2panel`), **sin contraseñas, tokens ni IPs públicas**.
- **Cambios en el código:** abre un [pull request](../../pulls). Antes de un cambio grande, comenta la idea en un issue.
- **Seguridad:** si encuentras una vulnerabilidad, no la publiques en un issue abierto. Usa *Security → Report a vulnerability* del repositorio, si está disponible, o abre un issue pidiendo un contacto privado sin dar detalles.

Respondo cuando puedo; no hay plazos garantizados. El proyecto se publica con licencia [PolyForm Noncommercial 1.0.0](LICENSE).

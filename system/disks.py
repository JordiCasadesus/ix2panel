import psutil
import platform
import re
import subprocess
import json
import os


def get_disk_from_device_linux(device):
    """
    /dev/sda1 → 0
    /dev/sdb2 → 1
    /dev/nvme0n1p1 → 0
    """
    if not device or not device.startswith("/dev/"):
        return "?"

    import re
    base = device.replace("/dev/", "")

    # nvme0n1p1 → nvme0n1
    base = re.sub(r'p?\d+$', '', base)

    if base.startswith("sd"):
        return str(ord(base[2]) - ord('a'))

    if base.startswith("hd"):
        return str(ord(base[2]) - ord('a'))

    if base.startswith("nvme"):
        m = re.search(r'nvme(\d+)', base)
        if m:
            return m.group(1)

    if base.startswith("mmcblk"):
        m = re.search(r'mmcblk(\d+)', base)
        if m:
            return str(int(m.group(1)) + 100)

    return "?"


def get_physical_disk_number(part):
    system = platform.system().lower()

    if system == "linux":
        return get_disk_from_device_linux(part.device)

    # Windows / otros → no fiable
    return "?"



def classify_partition(part):
    """Clasifica el tipo de partición según su mountpoint y filesystem."""
    mount = part.mountpoint if part.mountpoint else ""
    fstype = (part.fstype or "").lower()
    device = (part.device or "").lower()
    
    # Sistema
    if mount in ['/', '/boot', '/boot/efi']:
        return "Sistema"
    
    # Datos
    if mount.startswith('/data') or mount.startswith('/mnt'):
        return "Datos"
    
    # USB/External
    if mount.startswith('/media') or 'usb' in mount.lower():
        return "USB"
    
    # EFI
    if fstype in ("vfat", "fat32", "fat", "fat16") and "/boot" in mount.lower():
        return "EFI"
    
    # Detectar USB por filesystem FAT
    if fstype in ("vfat", "fat32", "fat", "fat16", "exfat", "ntfs"):
        return "USB"
    
    # Otros sistemas de archivos Linux
    if fstype in ("ext4", "ext3", "ext2", "btrfs", "xfs"):
        return "Datos"
    
    return "Datos"


def classify_partition_parts(mountpoint, fstype, device):
    class Obj:
        def __init__(self, mountpoint, fstype, device):
            self.mountpoint = mountpoint
            self.fstype = fstype
            self.device = device
    return classify_partition(Obj(mountpoint, fstype, device))

#Para discos dashboard
def get_disks():

    result = []
    system = platform.system().lower()

    for part in psutil.disk_partitions(all=False):

        if not part.mountpoint:
            continue

        try:
            usage = psutil.disk_usage(part.mountpoint)
        except Exception:
            continue

        used_gb = usage.used / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        percent = int((used_gb / total_gb) * 100) if total_gb else 0

        if percent < 80:
            status = "ok"
        elif percent < 90:
            status = "warn"
        else:
            status = "err"

        # =========================
        # LINUX / NAS (REAL)
        # =========================
        if system == "linux":
            disk_num = get_disk_from_device_linux(part.device)
            unit = part.mountpoint
            role = classify_partition(part)

            label = (
                f"DISCO {disk_num} – {unit}"
                if disk_num != "?"
                else unit
            )

        # =========================
        # WINDOWS (DEV)
        # =========================
        else:
            disk_num = "?"
            unit = part.mountpoint.rstrip("\\")
            role = "Datos"
            label = unit

        result.append({
            "disk": disk_num,
            "unit": unit,
            "label": label,
            "used": round(used_gb, 1),
            "total": round(total_gb, 1),
            "percent": percent,
            "status": status,
            "role": role,
            "fstype": part.fstype,
            "device": part.device
        })

    return result

#Para la pestaña DISCOS - Vista árbol
def get_disks_tree():
    system = platform.system().lower()
    disks = {}

    for part in psutil.disk_partitions(all=False):

        if not part.mountpoint:
            continue

        try:
            usage = psutil.disk_usage(part.mountpoint)
        except Exception:
            continue

        used_gb = usage.used / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        percent = int((used_gb / total_gb) * 100) if total_gb else 0

        # Estado de la partición
        if percent < 80:
            status = "ok"
        elif percent < 90:
            status = "warn"
        else:
            status = "err"

        # NAS / Linux
        if system == "linux":
            disk_num = get_disk_from_device_linux(part.device)
            role = classify_partition(part)
            name = part.mountpoint
        else:
            disk_num = "?"
            role = "Datos"
            name = part.mountpoint.rstrip("\\")

        if disk_num not in disks:
            disks[disk_num] = {
                "disk": disk_num,
                "device": disk_device,
                "label": f"DISCO {disk_num}",
                "used": 0.0,
                "total": 0.0,
                "percent": 0,
                "status": "ok",
                "partitions": []
            }

        disks[disk_num]["partitions"].append({
            "name": name,
            "device": part.device,
            "used": round(used_gb, 1),
            "total": round(total_gb, 1),
            "percent": percent,
            "role": role,
            "fstype": part.fstype
        })

    # Totales por disco
    for d in disks.values():
        d["used"] = round(sum(p["used"] for p in d["partitions"]), 1)
        d["total"] = round(sum(p["total"] for p in d["partitions"]), 1)
        d["percent"] = int((d["used"] / d["total"]) * 100) if d["total"] else 0

        if d["percent"] < 80:
            d["status"] = "ok"
        elif d["percent"] < 90:
            d["status"] = "warn"
        else:
            d["status"] = "err"

    return list(disks.values())


def _lsblk():
    try:
        out = subprocess.check_output(
            ["lsblk", "-J", "-b", "-o", "NAME,TYPE,SIZE,FSTYPE,MOUNTPOINT,UUID,RM"],
            text=True,
            errors="replace"
        )
        return json.loads(out or "{}")
    except Exception:
        return {}


def get_disks_tree_all():
    system = platform.system().lower()
    disks = {}

    # Mounted partitions usage
    usage_map = {}
    mount_map = {}
    for part in psutil.disk_partitions(all=True):
        if not part.mountpoint:
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except Exception:
            continue
        usage_map[part.device] = usage
        mount_map[part.device] = {
            "mountpoint": part.mountpoint,
            "fstype": part.fstype
        }

    lsblk = _lsblk()
    for dev in (lsblk.get("blockdevices") or []):
        if dev.get("type") != "disk":
            continue
        disk_name = dev.get("name") or ""
        disk_device = f"/dev/{disk_name}"
        disk_num = get_disk_from_device_linux(disk_device) if system == "linux" else "?"
        if disk_num not in disks:
            disks[disk_num] = {
                "disk": disk_num,
                "label": f"DISCO {disk_num}",
                "used": 0.0,
                "total": 0.0,
                "percent": 0,
                "status": "ok",
                "partitions": []
            }

        parts = dev.get("children") or []
        for p in parts:
            if p.get("type") != "part":
                continue
            name = p.get("name") or ""
            device = f"/dev/{name}"
            mountpoint = p.get("mountpoint") or ""
            fstype = p.get("fstype") or ""
            if not mountpoint and device in mount_map:
                mountpoint = mount_map[device]["mountpoint"] or ""
            if not fstype and device in mount_map:
                fstype = mount_map[device]["fstype"] or ""
            size_bytes = int(p.get("size") or 0)
            size_gb = round(size_bytes / (1024 ** 3), 1) if size_bytes else 0.0

            used_gb = 0.0
            total_gb = size_gb
            percent = 0
            status = "ok"
            mounted = bool(mountpoint)

            if mounted:
                usage = usage_map.get(device)
                if usage:
                    used_gb = usage.used / (1024 ** 3)
                    total_gb = usage.total / (1024 ** 3)
                    percent = int((used_gb / total_gb) * 100) if total_gb else 0
                    if percent < 80:
                        status = "ok"
                    elif percent < 90:
                        status = "warn"
                    else:
                        status = "err"
            role = classify_partition_parts(mountpoint, fstype, device) if mounted else "No montado"
            if p.get("rm") == 1:
                role = "USB"

            can_unmount = mounted and (mountpoint.startswith("/mnt") or mountpoint.startswith("/media"))
            can_mount = not mounted

            disks[disk_num]["partitions"].append({
                "name": mountpoint if mountpoint else device,
                "device": device,
                "mountpoint": mountpoint,
                "used": round(used_gb, 1),
                "total": round(total_gb, 1),
                "percent": percent,
                "role": role,
                "fstype": fstype,
                "mounted": mounted,
                "status": status,
                "can_mount": can_mount,
                "can_unmount": can_unmount
            })

    # Totals per disk
    for d in disks.values():
        total = 0.0
        used = 0.0
        for p in d["partitions"]:
            total += p.get("total", 0.0)
            used += p.get("used", 0.0)
        d["used"] = round(used, 1)
        d["total"] = round(total, 1)
        d["percent"] = int((d["used"] / d["total"]) * 100) if d["total"] else 0
        if d["percent"] < 80:
            d["status"] = "ok"
        elif d["percent"] < 90:
            d["status"] = "warn"
        else:
            d["status"] = "err"

    return list(disks.values())


def _mount_path_ok(path):
    """Solo subdirectorios de /mnt o /media (path ya con realpath)."""
    return any(path.startswith(root + "/") for root in ("/mnt", "/media"))


def mount_partition(device, mountpoint=""):
    if not re.fullmatch(r"/dev/(sd[a-z]+\d*|hd[a-z]+\d*|vd[a-z]+\d*|nvme\d+n\d+(p\d+)?|mmcblk\d+(p\d+)?|md\d+)", str(device)):
        return {"error": "Dispositivo inválido"}
    if not mountpoint:
        base = device.split("/")[-1]
        mountpoint = f"/mnt/{base}"
    mountpoint = os.path.realpath(mountpoint)
    if not _mount_path_ok(mountpoint):
        return {"error": "Punto de montaje no permitido (solo /mnt o /media)"}
    try:
        os.makedirs(mountpoint, exist_ok=True)
    except Exception as e:
        return {"error": str(e)}

    # Check if already mounted
    for p in psutil.disk_partitions(all=True):
        if p.device == device or p.mountpoint == mountpoint:
            return {"error": "Ya está montado"}
    try:
        subprocess.check_output(["sudo", "-n", "mount", device, mountpoint], text=True, errors="replace")
        return {"ok": True, "mountpoint": mountpoint}
    except Exception as e:
        return {"error": str(e)}


def unmount_partition(target):
    # target can be device or mountpoint
    mountpoint = target
    if target.startswith("/dev/"):
        for p in psutil.disk_partitions(all=True):
            if p.device == target:
                mountpoint = p.mountpoint
                break
    if not mountpoint:
        return {"error": "No montado"}
    if mountpoint in ("/", "/boot", "/data"):
        return {"error": "No se puede desmontar esta partición"}
    mountpoint = os.path.realpath(mountpoint)
    if not _mount_path_ok(mountpoint):
        return {"error": "Punto de montaje no permitido"}
    try:
        subprocess.check_output(["sudo", "-n", "umount", mountpoint], text=True, errors="replace")
        return {"ok": True}
    except Exception as e:
        return {"error": str(e)}

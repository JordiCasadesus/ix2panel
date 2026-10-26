import grp
import pwd
import re
import subprocess


GROUP_NAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


def get_groups():
    groups = {
        g.gr_gid: {
            "name": g.gr_name,
            "gid": g.gr_gid,
            "members": list(g.gr_mem),
            "members_primary": [],
            "members_secondary": list(g.gr_mem),
        }
        for g in grp.getgrall()
    }

    # Add users with this as primary group
    for u in pwd.getpwall():
        g = groups.get(u.pw_gid)
        if not g:
            continue
        if u.pw_name not in g["members"]:
            g["members"].append(u.pw_name)
        if u.pw_name not in g["members_primary"]:
            g["members_primary"].append(u.pw_name)

    result = list(groups.values())
    for g in result:
        g["members"] = sorted(set(g["members"]))
        g["members_primary"] = sorted(set(g["members_primary"]))
        g["members_secondary"] = sorted(set(g["members_secondary"]))
    return sorted(result, key=lambda x: x["name"])


def _valid_user(user):
    """Usuario con nombre razonable y que exista de verdad (evita pasar opciones a usermod/gpasswd)."""
    if not GROUP_NAME_RE.match(str(user)):
        return False
    try:
        pwd.getpwnam(user)
        return True
    except KeyError:
        return False


def create_group(name):
    if not GROUP_NAME_RE.match(name):
        return {"error": "Nombre de grupo inválido"}
    try:
        subprocess.check_output(["sudo", "groupadd", name], text=True, errors="replace")
        return {"ok": True}
    except Exception as e:
        return {"error": str(e)}


def add_user_to_group(user, group):
    if not GROUP_NAME_RE.match(group):
        return {"error": "Grupo inválido"}
    if not _valid_user(user):
        return {"error": "Usuario inválido"}
    try:
        subprocess.check_output(["sudo", "usermod", "-aG", group, user], text=True, errors="replace")
        return {"ok": True}
    except Exception as e:
        return {"error": str(e)}



def remove_user_from_group(user, group):
    if not GROUP_NAME_RE.match(group):
        return {"error": "Grupo inválido"}
    if not _valid_user(user):
        return {"error": "Usuario inválido"}
    try:
        subprocess.check_output(["sudo", "gpasswd", "-d", user, group], text=True, errors="replace")
        return {"ok": True}
    except Exception as e:
        return {"error": str(e)}

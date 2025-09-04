#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ansible Playbook CGI Runner — filtered inventories + regions/hosts + output + reports + switchuser option
- Hides file paths in UI (labels only)
- Inventory list filtered by selected playbook
- Regions (INI groups) + scrollable hosts + select all/none
- Intel: force SSH user cloudadmin
- AMD: SSH as serveradmin (default), sudo to awsuser (--become-user awsuser)
- Switch SSH user option (dropdown with common users + free-text)
- Runs ansible-playbook and shows output inline (masked command)
- Browse generated HTML reports securely
- Polished UI; Python 3.7 compatible

Changes:
- Expose SSH password as ansible_password + ansible_ssh_pass
- Add switchuser option in UI and runner logic
- Provide dropdown of common users with custom option
"""

import cgi
import cgitb
import html
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

cgitb.enable()

# ---------------- CONFIG ----------------
PLAYBOOKS = {
    "intel": {
        "label": "Intel Health Check",
        "path": "/var/www/cgi-bin/intel-check.yml",
        "inventories": ["intel-inv"],
        "force_ssh_user": "cloudadmin",
    },
    "amd": {
        "label": "AMD Health Check",
        "path": "/var/www/cgi-bin/amd-check.yml",
        "inventories": ["amd-inv"],
        "suggest_ssh_user": "serveradmin",
        "become_user": "awsuser",
    },
}

INVENTORIES = {
    "intel-inv": {"label": "Intel Inventory", "path": "/var/www/cgi-bin/intel-inv.ini"},
    "amd-inv":   {"label": "AMD Inventory",   "path": "/var/www/cgi-bin/amd-inv.ini"},
}

REPORT_BASES = ["/var/www/cgi-bin/reports"]

ANSIBLE_BIN = shutil.which("ansible-playbook") or "/usr/bin/ansible-playbook"
DEFAULT_USER = os.environ.get("ANSIBLE_SSH_USER", "ansadmin")
COMMON_USERS = ["cloudadmin", "serveradmin", "ansadmin", "ec2-user"]

RUN_TIMEOUT_SECS = 3600
USE_SUDO = False
SUDO_BIN = shutil.which("sudo") or "/usr/bin/sudo"

RUN_HOME = "/var/lib/www-ansible/home"
RUN_TMP  = "/var/lib/www-ansible/tmp"

HOST_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
USER_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
TAGS_RE = re.compile(r"^[A-Za-z0-9_,.-]+$")

# ---------------- UTIL ----------------
def header_ok(ct="text/html; charset=utf-8"):
    print("Content-Type: " + ct)
    print()

def safe(s: str) -> str:
    return html.escape("" if s is None else str(s))

def _realpath(p: str) -> str:
    return os.path.realpath(p)

def _is_under(base: str, target: str) -> bool:
    base_r = _realpath(base)
    tgt_r  = _realpath(target)
    return tgt_r == base_r or tgt_r.startswith(base_r + os.sep)

# ---------------- Inventory Parser ----------------
def parse_ini_inventory_groups(path: str):
    groups, current = {}, None
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith(("#",";")):
                continue
            if line.startswith("[") and line.endswith("]"):
                current = line[1:-1].strip()
                groups.setdefault(current, [])
                continue
            if current:
                token = line.split()[0].split("=")[0].strip()
                if token and token not in groups[current]:
                    groups[current].append(token)
    for k in ("all", "ungrouped"):
        if k in groups and not groups[k]:
            groups.pop(k, None)
    for k in groups:
        groups[k] = sorted(groups[k], key=str.lower)
    return dict(sorted(groups.items(), key=lambda kv: kv[0].lower()))

def get_inventory_maps(inv_key: str):
    meta = INVENTORIES.get(inv_key or "", {})
    path = meta.get("path", "")
    if not path:
        return {}, [], {}
    groups_map = parse_ini_inventory_groups(path)
    host_groups = {}
    for g, hosts in groups_map.items():
        for h in hosts:
            host_groups.setdefault(h, []).append(g)
    all_hosts = sorted(host_groups.keys(), key=str.lower)
    return groups_map, all_hosts, host_groups

# ---------------- Reports ----------------
def find_reports(hosts, since_ts, limit=200):
    out = []
    needles = [h.lower() for h in (hosts or [])]
    for base in REPORT_BASES:
        if not os.path.isdir(base):
            continue
        for root, _, files in os.walk(base):
            for fn in files:
                if not fn.lower().endswith(".html"):
                    continue
                full = os.path.join(root, fn)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                if st.st_mtime < since_ts:
                    continue
                if needles and not any(n in fn.lower() for n in needles):
                    continue
                rel = os.path.relpath(full, base)
                out.append({"base": base, "rel": rel, "path": full, "mtime": st.st_mtime})
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out[:limit]

def render_reports_list(title, reports, extra_note=""):
    items = []
    for r in reports:
        try:
            bidx = REPORT_BASES.index(r["base"])
        except ValueError:
            continue
        href = f"?action=view_report&b={bidx}&p={quote(r['rel'])}"
        ts   = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["mtime"]))
        items.append(f'<li><a href="{href}" target="_blank">{safe(r["rel"])} — {ts}</a></li>')
    if not items:
        ul = "<p class='muted'>No matching reports found.</p>"
    else:
        ul = "<ul>\n%s\n</ul>" % "\n".join(items)
    return f"<h3>{safe(title)}</h3>{ul}{('<p class=\'muted\'>%s</p>' % safe(extra_note) if extra_note else '')}"

# ---------------- Form ----------------
def render_form(msg: str = "", form: cgi.FieldStorage = None):
    header_ok()
    if form is None:
        form = cgi.FieldStorage()

    selected_playbook = form.getfirst("playbook", "")
    inventory_key     = form.getfirst("inventory_key", "")
    selected_regions  = form.getlist("regions")
    posted_hosts      = form.getlist("hosts")

    allowed_invs = PLAYBOOKS.get(selected_playbook, {}).get("inventories", [])

    groups_map, all_hosts, host_groups = get_inventory_maps(inventory_key)

    playbook_opts = "\n".join(
        f'<option value="{safe(k)}" {"selected" if k==selected_playbook else ""}>{safe(v["label"])}</option>'
        for k,v in PLAYBOOKS.items()
    )
    inv_opts = "\n".join(
        f'<option value="{safe(k)}" {"selected" if k==inventory_key else ""}>{safe(INVENTORIES[k]["label"])}</option>'
        for k in allowed_invs if k in INVENTORIES
    )

    if groups_map:
        regions_html = "\n".join(
            f'<label><input type="checkbox" name="regions" value="{safe(g)}" {"checked" if g in selected_regions else ""}/> {safe(g)} ({len(groups_map[g])})</label>'
            for g in groups_map
        )
    else:
        regions_html = "<p class='muted'>No regions to show. Select an inventory first.</p>"

    if all_hosts:
        hosts_html = "\n".join(
            f'<label><input type="checkbox" name="hosts" value="{safe(h)}" data-groups="{safe(",".join(host_groups.get(h, [])))}" {"checked" if posted_hosts and h in posted_hosts else ""}/> {safe(h)}</label>'
            for h in all_hosts
        )
    else:
        hosts_html = "<p class='muted'>No hosts to show.</p>"

    forced_user   = PLAYBOOKS.get(selected_playbook, {}).get("force_ssh_user")
    suggest_user  = PLAYBOOKS.get(selected_playbook, {}).get("suggest_ssh_user")
    preset = suggest_user if suggest_user else form.getfirst("user", DEFAULT_USER)

    if forced_user:
        user_input_html = (
            f'<input id="user" name="user_display" type="text" value="{safe(forced_user)}" disabled />'
            f'<input type="hidden" name="user" value="{safe(forced_user)}" />'
            f'<div class="muted">SSH login is forced to <strong>{safe(forced_user)}</strong> for this playbook.</div>'
        )
    else:
        opts = "\n".join(
            f'<option value="{safe(u)}" {"selected" if u==preset else ""}>{safe(u)}</option>'
            for u in COMMON_USERS
        )
        user_input_html = f"""
        <select name='user'>
        {opts}
        <option value='custom'>Custom...</option>
        </select>
        <input id='user_custom' name='user_custom' type='text' placeholder='Enter custom SSH user' style='display:none;margin-top:5px;' />
        <script>
        const sel=document.querySelector("select[name=user]");
        const inp=document.getElementById("user_custom");
        sel.addEventListener("change",()=>{{inp.style.display=(sel.value=="custom")?"block":"none";}});
        </script>
        """

    msg_html = f"<div class='warn'>{safe(msg)}</div>" if msg else ""

    print(f"""<!DOCTYPE html>
<html><head><meta charset='utf-8' />
<title>Ansible Playbook CGI Runner</title>
<style>body{{font-family:system-ui;}}</style>
</head><body>
<div class='card'>
<h1>Ansible Playbook CGI Runner</h1>
{msg_html}
<form method='post'>
<input type='hidden' name='action' id='action' value='refresh' />
<label>Playbook</label>
<select name='playbook' onchange="this.form.submit()">
<option value='' {'selected' if not selected_playbook else ''}>Select...</option>
{playbook_opts}
</select>

<label>Inventory</label>
<select name='inventory_key' onchange="this.form.submit()">
<option value=''>Select...</option>
{inv_opts}
</select>

<label>Regions</label>
<div>{regions_html}</div>
<label>Hosts</label>
<div class='hosts-box'>{hosts_html}</div>

<label>SSH User (switch user)</label>
{user_input_html}

<label>SSH Password</label>
<input name='password' type='password'/>
<label>Become Password</label>
<input name='become_pass' type='password'/>

<label><input type='checkbox' name='check' value='1' {"checked" if form.getfirst('check') else ''}/> Dry Run (--check)</label>
<label><input type='checkbox' name='become' value='1' {"checked" if form.getfirst('become') else ''}/> Become (-b)</label>

<div><button type='submit' onclick="document.getElementById('action').value='run'">Run Playbook</button></div>
</form></div></body></html>""")

# ---------------- Run ----------------
def run_playbook(form: cgi.FieldStorage):
    playbook_key = form.getfirst("playbook", "")
    inventory_key = form.getfirst("inventory_key", "")
    hosts = form.getlist("hosts")
    user  = (form.getfirst("user") or DEFAULT_USER).strip()
    if user == "custom":
        user = (form.getfirst("user_custom") or DEFAULT_USER).strip()
    tags  = (form.getfirst("tags") or "").strip()
    do_check  = (form.getfirst("check") == "1")
    do_become = (form.getfirst("become") == "1")
    ssh_pass    = (form.getfirst("password") or "").strip()
    become_pass = (form.getfirst("become_pass") or "").strip()

    if playbook_key not in PLAYBOOKS:
        render_form("Invalid playbook.", form); return
    if inventory_key not in INVENTORIES or inventory_key not in PLAYBOOKS[playbook_key]["inventories"]:
        render_form("Invalid inventory.", form); return
    if not hosts:
        render_form("No hosts selected.", form); return
    for h in hosts:
        if not HOST_RE.match(h):
            render_form(f"Invalid host {h}.", form); return
    if tags and not TAGS_RE.match(tags):
        render_form("Invalid tags.", form); return

    pb_meta = PLAYBOOKS[playbook_key]
    forced_user  = pb_meta.get("force_ssh_user")
    suggest_user = pb_meta.get("suggest_ssh_user")
    become_user  = pb_meta.get("become_user")

    if forced_user:
        user = forced_user
    elif suggest_user and not form.getfirst("user"):
        user = suggest_user
    if not USER_RE.match(user):
        render_form("Invalid SSH user.", form); return

    playbook_path  = pb_meta["path"]
    inventory_path = INVENTORIES[inventory_key]["path"]

    Path(RUN_HOME).mkdir(parents=True, exist_ok=True)
    Path(RUN_TMP).mkdir(parents=True, exist_ok=True)
    local_tmp = os.path.join(RUN_TMP, "ansible-local")
    Path(local_tmp).mkdir(parents=True, exist_ok=True)

    cmd = [ANSIBLE_BIN, "-i", inventory_path, playbook_path, "--limit", ",".join(hosts), "-u", user]
    if do_check:
        cmd.append("--check")
    if become_user:
        cmd += ["-b", "--become-user", become_user]
    elif do_become:
        cmd.append("-b")
    if tags:
        cmd += ["--tags", tags]
    if ssh_pass:
        cmd += ["-e", f"ansible_password={ssh_pass}", "-e", f"ansible_ssh_pass={ssh_pass}"]
    if become_pass:
        cmd += ["-e", f"ansible_become_password={become_pass}"]
    if USE_SUDO:
        cmd = [SUDO_BIN, "-n", "--"] + cmd

    env = os.environ.copy()
    env.update({
        "LANG": "C.UTF-8",
        "HOME": RUN_HOME,
        "TMPDIR": RUN_TMP,
        "ANSIBLE_LOCAL_TEMP": local_tmp,
        "ANSIBLE_REMOTE_TMP": "/tmp",
        "ANSIBLE_HOST_KEY_CHECKING": "False",
        "ANSIBLE_SSH_ARGS": "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null",
    })

    TEXT_KW = {"text": True} if sys.version_info >= (3,7) else {"universal_newlines": True}

    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, timeout=RUN_TIMEOUT_SECS, cwd=Path(playbook_path).parent, **TEXT_KW)
        output, rc = proc.stdout, proc.returncode
    except subprocess.TimeoutExpired:
        output, rc = f"Timeout after {RUN_TIMEOUT_SECS}s", 124
    except Exception as e:
        header_ok(); print(f"<pre>{safe(str(e))}</pre>"); return

    header_ok()
    status = "✅ SUCCESS" if rc==0 else f"❌ FAILED rc={rc}"
    print(f"""<html><body><div class='card'>

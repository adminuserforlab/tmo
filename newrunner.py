#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ansible Playbook CGI Runner — Regions + Hosts + Output
- Regions (INI groups) and multi-host selection
- Region toggles auto-select/clear their hosts
- Scrollable hosts list (compact UI)
- Runs ansible-playbook and shows output in an HTML file
- Python 3.7 compatible
"""

import cgi
import cgitb
import html
import os
import re
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

cgitb.enable()

# ---------------- CONFIG ----------------
PLAYBOOKS = {
    "test-pb": "/var/pb/test-playbook.yml",
    # add more as needed
}
INVENTORIES = {
    "test-inv": "/var/pb/inv.ini",  # your INI with [Chicago], [New York], [California]
    # add more as needed
}

ANSIBLE_BIN = shutil.which("ansible-playbook") or "/usr/bin/ansible-playbook"
DEFAULT_USER = os.environ.get("ANSIBLE_SSH_USER", "ansadmin")
RUN_TIMEOUT_SECS = 3600

USE_SUDO = False
SUDO_BIN = shutil.which("sudo") or "/usr/bin/sudo"

# --- SAFER PATHS (writable by web user) ---
RUN_HOME = "/tmp/www-ansible/home"
RUN_TMP = "/tmp/www-ansible/tmp"

# Ensure base dirs exist
Path(RUN_HOME).mkdir(parents=True, exist_ok=True)
Path(RUN_TMP).mkdir(parents=True, exist_ok=True)

# Validators
HOST_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
USER_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
TAGS_RE = re.compile(r"^[A-Za-z0-9_,.-]+$")


# ---------------- UTIL ----------------
def header_ok():
    print("Content-Type: text/html; charset=utf-8")
    print()


def safe(s: str) -> str:
    return html.escape(s or "")


def parse_ini_inventory_groups(path: str):
    groups = {}
    current = None
    if not os.path.exists(path):
        return {}

    with open(path, "r") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith(";"):
                continue
            if line.startswith("[") and line.endswith("]"):
                current = line[1:-1].strip()
                groups.setdefault(current, [])
                continue
            if current:
                token = line.split()[0].split("=")[0].strip()
                if token:
                    if token not in groups[current]:
                        groups[current].append(token)

    for k in ("all", "ungrouped"):
        if k in groups and not groups[k]:
            groups.pop(k, None)

    for k in groups:
        groups[k] = sorted(groups[k])
    return dict(sorted(groups.items(), key=lambda kv: kv[0].lower()))


def get_inventory_maps(inv_key: str):
    path = INVENTORIES.get(inv_key or "", "")
    if not path:
        return {}, [], {}
    groups_map = parse_ini_inventory_groups(path)

    host_groups = {}
    for g, hosts in groups_map.items():
        for h in hosts:
            host_groups.setdefault(h, []).append(g)

    all_hosts = sorted(host_groups.keys(), key=str.lower)
    return groups_map, all_hosts, host_groups


# ---------------- RENDER ----------------
def render_form(msg: str, form: cgi.FieldStorage):
    header_ok()
    print("<html><head><title>Ansible Runner</title></head><body>")
    if msg:
        print("<p style='color:red;'><b>{}</b></p>".format(safe(msg)))

    print("<h2>Run Ansible Playbook</h2>")
    print('<form method="post">')
    print('<input type="hidden" name="action" value="run">')

    # Playbooks
    print("<p>Playbook:<br>")
    print('<select name="playbook">')
    for k in PLAYBOOKS:
        sel = " selected" if form.getfirst("playbook") == k else ""
        print('<option value="{}"{}>{}</option>'.format(safe(k), sel, safe(k)))
    print("</select></p>")

    # Inventories
    print("<p>Inventory:<br>")
    print('<select name="inventory_key">')
    for k in INVENTORIES:
        sel = " selected" if form.getfirst("inventory_key") == k else ""
        print('<option value="{}"{}>{}</option>'.format(safe(k), sel, safe(k)))
    print("</select></p>")

    print("<p>Hosts (comma separated):<br>")
    print('<input type="text" name="hosts" value="{}">'.format(safe(",".join(form.getlist("hosts")))))
    print("</p>")

    print("<p>User:<br>")
    print('<input type="text" name="user" value="{}">'.format(safe(form.getfirst("user") or DEFAULT_USER)))
    print("</p>")

    print("<p>Tags:<br>")
    print('<input type="text" name="tags" value="{}">'.format(safe(form.getfirst("tags") or "")))
    print("</p>")

    print('<p><label><input type="checkbox" name="check" value="1"> Check mode</label></p>')
    print('<p><label><input type="checkbox" name="become" value="1"> Become (sudo)</label></p>')

    print("<p>Password:<br>")
    print('<input type="password" name="password"></p>')

    print("<p>Become Password:<br>")
    print('<input type="password" name="become_pass"></p>')

    print('<p><input type="submit" value="Run"></p>')
    print("</form></body></html>")


# ---------------- RUN ----------------
def run_playbook(form: cgi.FieldStorage):
    playbook_key = form.getfirst("playbook", "")
    inventory_key = form.getfirst("inventory_key", "")
    hosts = (form.getfirst("hosts") or "").split(",")
    hosts = [h.strip() for h in hosts if h.strip()]
    user = (form.getfirst("user") or DEFAULT_USER).strip()
    tags = (form.getfirst("tags") or "").strip()
    do_check = (form.getfirst("check") == "1")
    do_become = (form.getfirst("become") == "1")
    ssh_pass = (form.getfirst("password") or "").strip()
    become_pass = (form.getfirst("become_pass") or "").strip()

    if playbook_key not in PLAYBOOKS:
        render_form("Invalid playbook selected.", form)
        return
    if inventory_key not in INVENTORIES:
        render_form("Invalid inventory selected.", form)
        return
    if not hosts:
        render_form("No hosts selected.", form)
        return
    for h in hosts:
        if not HOST_RE.match(h):
            render_form("Invalid hostname: {}".format(h), form)
            return
    if not USER_RE.match(user):
        render_form("Invalid SSH user.", form)
        return
    if tags and not TAGS_RE.match(tags):
        render_form("Invalid characters in tags.", form)
        return

    playbook_path = PLAYBOOKS[playbook_key]
    inventory_path = INVENTORIES[inventory_key]

    Path(RUN_HOME).mkdir(parents=True, exist_ok=True)
    Path(RUN_TMP).mkdir(parents=True, exist_ok=True)
    local_tmp = os.path.join(RUN_TMP, "ansible-local")
    Path(local_tmp).mkdir(parents=True, exist_ok=True)

    cmd = [ANSIBLE_BIN, "-i", inventory_path, playbook_path, "--limit", ",".join(hosts), "-u", user]
    if do_check:
        cmd.append("--check")
    if do_become:
        cmd.append("-b")
    if tags:
        cmd += ["--tags", tags]
    if ssh_pass:
        cmd += ["-e", "ansible_password={}".format(ssh_pass)]
    if become_pass:
        cmd += ["-e", "ansible_become_password={}".format(become_pass)]

    if USE_SUDO:
        cmd = [SUDO_BIN, "-n", "--"] + cmd

    env = os.environ.copy()
    env["LANG"] = "C.UTF-8"
    env["HOME"] = RUN_HOME
    env["TMPDIR"] = RUN_TMP
    env["ANSIBLE_LOCAL_TEMP"] = local_tmp
    env["ANSIBLE_REMOTE_TMP"] = "/tmp"
    env["ANSIBLE_HOST_KEY_CHECKING"] = "False"
    env["ANSIBLE_SSH_ARGS"] = "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

    TEXT_KW = {"text": True} if sys.version_info >= (3, 7) else {"universal_newlines": True}

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            timeout=RUN_TIMEOUT_SECS,
            cwd=Path(playbook_path).parent,
            **TEXT_KW
        )
        output = proc.stdout
        rc = proc.returncode
    except subprocess.TimeoutExpired as e:
        output = (e.output or "") + "\nERROR: Execution timed out after {}s.\n".format(RUN_TIMEOUT_SECS)
        rc = 124
    except Exception as e:
        header_ok()
        print("<pre>{}</pre>".format(safe(str(e))))
        return

    # ---- Write HTML output file ----
    html_output_path = os.path.join(RUN_HOME, "ansible_result.html")
    try:
        with open(html_output_path, "w", encoding="utf-8") as f:
            f.write("<html><head><meta charset='utf-8'>")
            f.write("<title>Ansible Playbook Result</title>")
            f.write("<style>")
            f.write("body { font-family: monospace; background: #1e1e1e; color: #dcdcdc; padding: 20px; }")
            f.write("h2 { color: #4ec9b0; }")
            f.write("pre { white-space: pre-wrap; word-wrap: break-word; background: #252526; padding: 15px; border-radius: 8px; }")
            f.write("</style></head><body>")
            f.write("<h2>Playbook Result — {}</h2>".format(safe(playbook_key)))
            f.write("<p><b>Return Code:</b> {}</p>".format(rc))
            f.write("<pre>{}</pre>".format(safe(output)))
            f.write("</body></html>")
    except Exception as e:
        header_ok()
        print("<pre>Failed to write HTML report: {}</pre>".format(safe(str(e))))
        return

    # ---- Serve HTML back to browser ----
    header_ok()
    with open(html_output_path, "r", encoding="utf-8") as f:
        print(f.read())


# ---------------- MAIN ----------------
def main():
    try:
        method = os.environ.get("REQUEST_METHOD", "GET").upper()
        form = cgi.FieldStorage()
        if method == "POST" and form.getfirst("action") == "run":
            run_playbook(form)
        else:
            render_form("", form)
    except Exception:
        header_ok()
        print("<pre>{}</pre>".format(safe(traceback.format_exc())))


if __name__ == "__main__":
    main()

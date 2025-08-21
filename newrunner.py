#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ansible Playbook CGI Runner — Regions + Hosts + Output
- Saves ansible-playbook output into /tmp/ansible_result.html
- Redirects browser to that HTML file
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
}
INVENTORIES = {
    "test-inv": "/var/pb/inv.ini",
}

ANSIBLE_BIN = shutil.which("ansible-playbook") or "/usr/bin/ansible-playbook"
DEFAULT_USER = os.environ.get("ANSIBLE_SSH_USER", "ansadmin")
RUN_TIMEOUT_SECS = 3600

USE_SUDO = False
SUDO_BIN = shutil.which("sudo") or "/usr/bin/sudo"

# --- Output file ---
HTML_RESULT_FILE = "/tmp/ansible_result.html"

# Validators
HOST_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
USER_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
TAGS_RE = re.compile(r"^[A-Za-z0-9_,.-]+$")


# ---------------- UTIL ----------------
def safe(s: str) -> str:
    return html.escape(s or "")


# ---------------- RENDER ----------------
def render_form(msg: str, form: cgi.FieldStorage):
    print("Content-Type: text/html; charset=utf-8\n")
    print("<html><head><title>Ansible Runner</title></head><body>")
    if msg:
        print("<p style='color:red;'><b>{}</b></p>".format(safe(msg)))

    print("<h2>Run Ansible Playbook</h2>")
    print('<form method="post">')
    print('<input type="hidden" name="action" value="run">')

    print("<p>Playbook:<br><select name='playbook'>")
    for k in PLAYBOOKS:
        sel = " selected" if form.getfirst("playbook") == k else ""
        print(f"<option value='{safe(k)}'{sel}>{safe(k)}</option>")
    print("</select></p>")

    print("<p>Inventory:<br><select name='inventory_key'>")
    for k in INVENTORIES:
        sel = " selected" if form.getfirst("inventory_key") == k else ""
        print(f"<option value='{safe(k)}'{sel}>{safe(k)}</option>")
    print("</select></p>")

    print(f"<p>Hosts:<br><input type='text' name='hosts' value='{safe(','.join(form.getlist('hosts')))}'></p>")
    print(f"<p>User:<br><input type='text' name='user' value='{safe(form.getfirst('user') or DEFAULT_USER)}'></p>")
    print(f"<p>Tags:<br><input type='text' name='tags' value='{safe(form.getfirst('tags') or '')}'></p>")
    print("<p><label><input type='checkbox' name='check' value='1'> Check mode</label></p>")
    print("<p><label><input type='checkbox' name='become' value='1'> Become (sudo)</label></p>")
    print("<p>Password:<br><input type='password' name='password'></p>")
    print("<p>Become Password:<br><input type='password' name='become_pass'></p>")
    print("<p><input type='submit' value='Run'></p></form></body></html>")


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
            render_form(f"Invalid hostname: {h}", form)
            return
    if not USER_RE.match(user):
        render_form("Invalid SSH user.", form)
        return
    if tags and not TAGS_RE.match(tags):
        render_form("Invalid characters in tags.", form)
        return

    playbook_path = PLAYBOOKS[playbook_key]
    inventory_path = INVENTORIES[inventory_key]

    cmd = [ANSIBLE_BIN, "-i", inventory_path, playbook_path, "--limit", ",".join(hosts), "-u", user]
    if do_check:
        cmd.append("--check")
    if do_become:
        cmd.append("-b")
    if tags:
        cmd += ["--tags", tags]
    if ssh_pass:
        cmd += ["-e", f"ansible_password={ssh_pass}"]
    if become_pass:
        cmd += ["-e", f"ansible_become_password={become_pass}"]

    if USE_SUDO:
        cmd = [SUDO_BIN, "-n", "--"] + cmd

    env = os.environ.copy()
    env["LANG"] = "C.UTF-8"
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
        output = (e.output or "") + f"\nERROR: Execution timed out after {RUN_TIMEOUT_SECS}s.\n"
        rc = 124
    except Exception as e:
        print("Content-Type: text/html; charset=utf-8\n")
        print(f"<pre>{safe(str(e))}</pre>")
        return

    # ---- Write HTML file in /tmp ----
    try:
        with open(HTML_RESULT_FILE, "w", encoding="utf-8") as f:
            f.write("<html><head><meta charset='utf-8'>")
            f.write("<title>Ansible Playbook Result</title>")
            f.write("<style>")
            f.write("body { font-family: monospace; background: #1e1e1e; color: #dcdcdc; padding: 20px; }")
            f.write("h2 { color: #4ec9b0; }")
            f.write("pre { white-space: pre-wrap; word-wrap: break-word; background: #252526; padding: 15px; border-radius: 8px; }")
            f.write("</style></head><body>")
            f.write(f"<h2>Playbook Result — {safe(playbook_key)}</h2>")
            f.write(f"<p><b>Return Code:</b> {rc}</p>")
            f.write(f"<pre>{safe(output)}</pre>")
            f.write("</body></html>")
    except Exception as e:
        print("Content-Type: text/html; charset=utf-8\n")
        print(f"<pre>Failed to write HTML report: {safe(str(e))}</pre>")
        return

    # ---- Redirect browser ----
    print("Status: 302 Found")
    print("Location: /ansible_result.html")  # must be served by webserver
    print()


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
        print("Content-Type: text/html; charset=utf-8\n")
        print(f"<pre>{safe(traceback.format_exc())}</pre>")


if __name__ == "__main__":
    main()

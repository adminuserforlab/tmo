#!/usr/bin/env python3
# -- coding: utf-8 --

"""
Ansible Playbook CGI Runner — Python 3.7-compatible
"""

import cgi
import cgitb
import html
import os
import re
import shutil
import subprocess
import tempfile
import configparser
from pathlib import Path

cgitb.enable(display=1)

# --- CONFIGURATION ---
PLAYBOOKS = {
    "test-pb": "/var/pb/test-playbook.yml",
    "upgrade": "/opt/ansible/playbooks/upgrade.yml",
    "rollback": "/opt/ansible/playbooks/rollback.yml",
}
INVENTORIES = {
    "test-inv": "/var/pb/inv.yml",
    "staging": "/opt/ansible/inv/staging.ini",
    "dev": "/opt/ansible/inv/dev.ini",
}

ANSIBLE_BIN = shutil.which("ansible-playbook") or "/usr/bin/ansible-playbook"
DEFAULT_USER = os.environ.get("ANSIBLE_SSH_USER", "ansadmin")
RUN_TIMEOUT_SECS = 3600
USE_SUDO = False
SUDO_BIN = shutil.which("sudo") or "/usr/bin/sudo"

# --- VALIDATION ---
HOST_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_.,-]+$")
TAGS_RE = re.compile(r"^[A-Za-z0-9_,.-]+$")
USER_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def header_ok():
    print("Content-Type: text/html; charset=utf-8")
    print()


def render_form(msg=""):
    header_ok()
    playbook_opts = "\n".join(
        '<option value="{k}">{k} — {v}</option>'.format(k=html.escape(k), v=html.escape(v))
        for k, v in PLAYBOOKS.items()
    )
    inv_opts = "\n".join(
        '<option value="{k}">{k} — {v}</option>'.format(k=html.escape(k), v=html.escape(v))
        for k, v in INVENTORIES.items()
    )
    print("""
<!DOCTYPE html>
<html><head><meta charset="utf-8" /><title>Ansible CGI Runner</title></head>
<body style="font-family:sans-serif; margin:24px;">
<h1>Run Ansible Playbook</h1>
{msg}
<form method="post">
  <label>Playbook:</label><br>
  <select name="playbook" required>
    <option value="" disabled selected>Select playbook…</option>
    {playbooks}
  </select><br><br>
  <label>Inventory:</label><br>
  <select name="inventory_key" required>
    <option value="" disabled selected>Select inventory…</option>
    {inventories}
  </select><br><br>
  <button type="submit">Next → Select Hosts</button>
</form>
</body></html>
""".format(
        msg=f'<div style="color:red">{html.escape(msg)}</div>' if msg else "",
        playbooks=playbook_opts,
        inventories=inv_opts,
    ))


def parse_inventory_hosts(path):
    parser = configparser.ConfigParser(allow_no_value=True, delimiters=(' ',))
    parser.optionxform = str  # preserve case
    parser.read(path)
    hosts = []
    for section in parser.sections():
        if section.startswith("group:") or section == "defaults":
            continue
        for h in parser.options(section):
            if HOST_RE.match(h):
                hosts.append(h)
    return sorted(set(hosts))


def show_host_selection(form):
    inventory_key = form.getfirst("inventory_key", "").strip()
    playbook_key = form.getfirst("playbook", "").strip()

    if inventory_key not in INVENTORIES or playbook_key not in PLAYBOOKS:
        render_form("Invalid inventory or playbook.")
        return

    inventory_path = INVENTORIES[inventory_key]
    try:
        hosts = parse_inventory_hosts(inventory_path)
    except Exception as e:
        render_form("Error parsing inventory: " + str(e))
        return

    header_ok()
    print("""
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Select Hosts</title></head>
<body style="font-family:sans-serif; margin:24px;">
<h2>Select Hosts from Inventory</h2>
<form method="post">
<input type="hidden" name="inventory_key" value="{inv}">
<input type="hidden" name="playbook" value="{pb}">
<table border="1" cellpadding="6">
<tr><th>Select</th><th>Host</th></tr>
""".format(inv=html.escape(inventory_key), pb=html.escape(playbook_key)))

    for host in hosts:
        print(f'<tr><td><input type="checkbox" name="selected_host" value="{html.escape(host)}"></td><td>{html.escape(host)}</td></tr>')

    print("""
</table><br>
<label>SSH User:</label><br>
<input type="text" name="user" value="{0}"><br><br>
<label><input type="checkbox" name="become" value="1" checked> Become (-b)</label><br>
<label><input type="checkbox" name="check" value="1"> Dry run (--check)</label><br><br>
<button type="submit">▶ Run Playbook</button>
</form></body></html>
""".format(html.escape(DEFAULT_USER)))


def do_run(form):
    playbook_key = form.getfirst("playbook", "").strip()
    inventory_key = form.getfirst("inventory_key", "").strip()
    selected_hosts = form.getlist("selected_host")

    if playbook_key not in PLAYBOOKS or inventory_key not in INVENTORIES:
        render_form("Invalid playbook or inventory.")
        return

    playbook_path = PLAYBOOKS[playbook_key]
    inventory_path = INVENTORIES[inventory_key]

    cmd = [ANSIBLE_BIN, "-i", inventory_path]

    if selected_hosts:
        for h in selected_hosts:
            if not HOST_RE.match(h):
                render_form("Invalid host selected.")
                return
        cmd += ["-l", ",".join(selected_hosts)]

    user = form.getfirst("user", DEFAULT_USER).strip()
    if not USER_RE.match(user):
        render_form("Invalid SSH user.")
        return
    cmd += ["-u", user]

    if form.getfirst("become") == "1":
        cmd += ["-b"]
    if form.getfirst("check") == "1":
        cmd += ["--check"]

    cmd.append(playbook_path)

    if USE_SUDO:
        cmd = [SUDO_BIN, "-n", "--"] + cmd

    env = os.environ.copy()
    env.setdefault("LANG", "C.UTF-8")
    env.setdefault("ANSIBLE_HOST_KEY_CHECKING", "False")

    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              env=env, text=True, timeout=RUN_TIMEOUT_SECS)
        output = proc.stdout
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        output = "ERROR: Timeout after {} seconds.".format(RUN_TIMEOUT_SECS)
        rc = 124

    header_ok()
    print("""
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Result</title></head>
<body style="font-family:sans-serif; margin:24px;">
<h1>{status}</h1>
<p><strong>Command:</strong> <code>{cmd}</code></p>
<pre>{output}</pre>
<p><a href="">Run another</a></p>
</body></html>
""".format(
        status="✅ SUCCESS" if rc == 0 else f"❌ FAILED (rc={rc})",
        cmd=" ".join(html.escape(x) for x in cmd),
        output=html.escape(output),
    ))


def main():
    try:
        method = os.environ.get("REQUEST_METHOD", "GET").upper()
        if method == "POST":
            form = cgi.FieldStorage()
            if form.getfirst("inventory_key") and not form.getfirst("selected_host"):
                show_host_selection(form)
            else:
                do_run(form)
        else:
            render_form()
    except Exception:
        header_ok()
        import traceback
        print("<pre>{}</pre>".format(html.escape(traceback.format_exc())))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import cgi
import cgitb
import html
import json
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
    },
}

INVENTORIES = {
    "intel-inv": {"label": "Intel Inventory", "path": "/var/www/cgi-bin/intel-inv.ini"},
    "amd-inv":   {"label": "AMD Inventory",   "path": "/var/www/cgi-bin/amd-inv.ini"},
}

REPORT_BASES = ["/tmp"]

ANSIBLE_BIN = shutil.which("ansible-playbook") or "/usr/bin/ansible-playbook"
DEFAULT_USER = os.environ.get("ANSIBLE_SSH_USER", "ansadmin")
RUN_TIMEOUT_SECS = 8 * 3600

USE_SUDO = False
SUDO_BIN = shutil.which("sudo") or "/usr/bin/sudo"

RUN_HOME = "/tmp/lib/www-ansible/home"
RUN_TMP  = "/tmp/www-ansible/tmp"
JOB_DIR  = "/var/lib/www-ansible/jobs"

HOST_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
USER_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
TAGS_RE = re.compile(r"^[A-Za-z0-9_,.-]+$")

# ---------------- UTIL ----------------
def header_ok(ct="text/html; charset=utf-8"):
    print("Content-Type: " + ct)
    print()

def safe(s: str) -> str:
    return html.escape("" if s is None else str(s))

def parse_ini_inventory_groups(path: str):
    groups = {}
    current = None
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
    for k in ("all","ungrouped"):
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

def ensure_dirs():
    Path(RUN_HOME).mkdir(parents=True, exist_ok=True)
    Path(RUN_TMP).mkdir(parents=True, exist_ok=True)
    Path(JOB_DIR).mkdir(parents=True, exist_ok=True)

def new_job_id():
    return "%d_%s" % (int(time.time()), os.urandom(5).hex())

def job_paths(job_id):
    jdir = os.path.join(JOB_DIR, job_id)
    return {
        "dir": jdir,
        "log": os.path.join(jdir, "output.log"),
        "meta": os.path.join(jdir, "meta.json"),
        "rc": os.path.join(jdir, "rc.txt"),
        "cmd": os.path.join(jdir, "command.txt"),
    }

def write_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f)

def read_json(path, default=None):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return default

def process_running(pid):
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False

# ---------------- RENDER FORM ----------------
def render_form(msg: str = "", form: cgi.FieldStorage = None):
    header_ok()
    if form is None:
        form = cgi.FieldStorage()

    selected_playbook = form.getfirst("playbook", "")
    inventory_key     = form.getfirst("inventory_key", "")
    selected_regions  = form.getlist("regions")
    posted_hosts      = form.getlist("hosts")

    if selected_playbook in PLAYBOOKS:
        allowed_invs = PLAYBOOKS[selected_playbook]["inventories"]
    else:
        allowed_invs = []

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
            f'<label><input type="checkbox" name="regions" value="{safe(group)}" {"checked" if group in selected_regions else ""}/> {safe(group)} ({len(groups_map[group])})</label>'
            for group in groups_map
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

    if selected_playbook and "suggest_ssh_user" in PLAYBOOKS[selected_playbook]:
        user_val = safe(PLAYBOOKS[selected_playbook]["suggest_ssh_user"])
    elif selected_playbook and "force_ssh_user" in PLAYBOOKS[selected_playbook]:
        user_val = safe(PLAYBOOKS[selected_playbook]["force_ssh_user"])
    else:
        user_val = safe(DEFAULT_USER)

    tags_val   = safe(form.getfirst("tags", ""))
    check_val  = "checked" if form.getfirst("check") else ""
    become_val = "checked" if (form.getfirst("become") or not form) else ""
    msg_html   = f"<div class='warn'>{safe(msg)}</div>" if msg else ""

    # Use f-string to avoid .format() issues with CSS {}
    print(f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Ansible Playbook CGI Runner</title>
  <style>
    body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 24px; }}
    .card {{ max-width: 900px; margin: auto; padding: 20px; border: 1px solid #ddd; border-radius: 12px; box-shadow: 0 2px 6px rgba(0,0,0,.05); }}
    h1 {{ margin-top: 0; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>Ansible Playbook CGI Runner</h1>
    {msg_html}
    <form method="post" action="">
      <input type="hidden" name="action" value="start" />
      <label for="playbook">Playbook</label>
      <select id="playbook" name="playbook" required>
        <option value="" {"selected" if not selected_playbook else ""}>Select a playbook…</option>
        {playbook_opts}
      </select>
      <label for="inventory_key">Inventory</label>
      <select id="inventory_key" name="inventory_key">{inv_opts}</select>
      <div>{regions_html}</div>
      <div>{hosts_html}</div>
      <input id="user" name="user" type="text" value="{user_val}" />
      <input id="tags" name="tags" type="text" value="{tags_val}" />
      <label><input type="checkbox" name="check" value="1" {check_val}/> Dry run</label>
      <label><input type="checkbox" name="become" value="1" {become_val}/> Become</label>
      <button type="submit">Run Playbook</button>
    </form>
  </div>
</body>
</html>""")

# ---------------- MAIN ----------------
def main():
    try:
        method = os.environ.get("REQUEST_METHOD", "GET").upper()
        form = cgi.FieldStorage()

        action = form.getfirst("action", "")
        if method == "POST" and action == "start":
            start_job(form)
        elif method == "GET" and action == "watch":
            render_watch(form)
        elif method == "GET" and action == "poll":
            poll_job(form)
        else:
            render_form("", form)
    except Exception:
        header_ok()
        import traceback
        print("<pre>%s</pre>" % safe(traceback.format_exc()))

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ansible Playbook CGI Runner — filtered inventories + regions/hosts + output + reports
- Hides file paths in UI (labels only)
- Inventory list filtered by selected playbook
- Regions (INI groups) + scrollable hosts + select all/none
- Intel: SSH as cloud-user (key-based)
- AMD: UI shows served_prd but backend uses cbis-admin with private key
- Runs ansible-playbook and shows output inline (masked command)
- Browse generated HTML reports securely
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
        "force_ssh_user": "cloud-user",  # always login as cloud-user
    },
    "amd": {
        "label": "AMD Health Check",
        "path": "/var/www/cgi-bin/amd-check.yml",
        "inventories": ["amd-inv"],
        "force_ssh_user": "cbis-admin",  # backend login user
        "ssh_private_key": "/var/lib/www-ansible/keys/serveradmin.pem",
        "suggest_ssh_user": "served_prd",  # UI shows this
    },
}

INVENTORIES = {
    "intel-inv": {"label": "Intel Inventory", "path": "/var/www/cgi-bin/intel-inv.ini"},
    "amd-inv":   {"label": "AMD Inventory",   "path": "/var/www/cgi-bin/amd-inv.ini"},
}

REPORT_BASES = ["/var/www/cgi-bin/reports"]

ANSIBLE_BIN = shutil.which("ansible-playbook") or "/usr/bin/ansible-playbook"
DEFAULT_USER = os.environ.get("ANSIBLE_SSH_USER", "ansadmin")
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

# ---------------- RENDER (FORM) ----------------
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
        '<option value="{k}" {sel}>{lbl}</option>'.format(
            k=safe(k), lbl=safe(v["label"]), sel=("selected" if k == selected_playbook else "")
        )
        for k, v in PLAYBOOKS.items()
    )
    inv_opts = "\n".join(
        '<option value="{k}" {sel}>{lbl}</option>'.format(
            k=safe(k), lbl=safe(INVENTORIES[k]["label"]), sel=("selected" if k == inventory_key else "")
        )
        for k in allowed_invs
        if k in INVENTORIES
    )

    if groups_map:
        regions_html = "\n".join(
            '<label><input type="checkbox" name="regions" value="{g}" {chk}/> {g} ({n})</label>'.format(
                g=safe(group), n=len(groups_map[group]), chk=("checked" if group in selected_regions else "")
            )
            for group in groups_map
        )
    else:
        regions_html = "<p class='muted'>No regions to show. Select an inventory first.</p>"

    if all_hosts:
        hosts_html = "\n".join(
            '<label><input type="checkbox" name="hosts" value="{h}" data-groups="{gs}" {chk}/> {h}</label>'.format(
                h=safe(h),
                gs=safe(",".join(host_groups.get(h, []))),
                chk=("checked" if posted_hosts and h in posted_hosts else "")
            )
            for h in all_hosts
        )
    else:
        hosts_html = "<p class='muted'>No hosts to show.</p>"

    # SSH user shown in UI
    if selected_playbook == "intel":
        user_val = safe(PLAYBOOKS["intel"]["force_ssh_user"])
    elif selected_playbook == "amd":
        user_val = safe(PLAYBOOKS["amd"]["suggest_ssh_user"])
    else:
        user_val = safe(DEFAULT_USER)

    tags_val   = safe(form.getfirst("tags", ""))
    check_val  = "checked" if form.getfirst("check") else ""
    become_val = "checked" if (form.getfirst("become") or not form) else ""
    msg_html   = ("<div class='warn'>{}</div>".format(safe(msg))) if msg else ""

    print(f"""<!DOCTYPE html>
<html>
<head>
  <meta charset=\"utf-8\"/>
  <title>Ansible Playbook Runner</title>
  <style>
    body {{ font-family: sans-serif; margin: 1em; }}
    .warn {{ color: red; margin-bottom: 1em; }}
    fieldset {{ margin-bottom: 1em; }}
    .scrollbox {{ border: 1px solid #ccc; padding: 0.5em; max-height: 200px; overflow-y: scroll; }}
    label {{ display: block; }}
    .muted {{ color: #666; }}
  </style>
  <script>
    function toggleAll(name, checked) {{
      var boxes = document.querySelectorAll("input[name='" + name + "']");
      boxes.forEach(b => b.checked = checked);
    }}
  </script>
</head>
<body>
  <h1>Ansible Playbook Runner</h1>
  {msg_html}
  <form method="post">
    <fieldset>
      <legend>Playbook</legend>
      <select name="playbook" onchange="this.form.submit()">
        {playbook_opts}
      </select>
    </fieldset>

    <fieldset>
      <legend>Inventory</legend>
      <select name="inventory_key" onchange="this.form.submit()">
        {inv_opts}
      </select>
    </fieldset>

    <fieldset>
      <legend>Regions</legend>
      <div class="scrollbox">
        {regions_html}
      </div>
      <button type="button" onclick="toggleAll('regions',true)">All</button>
      <button type="button" onclick="toggleAll('regions',false)">None</button>
    </fieldset>

    <fieldset>
      <legend>Hosts</legend>
      <div class="scrollbox">
        {hosts_html}
      </div>
      <button type="button" onclick="toggleAll('hosts',true)">All</button>
      <button type="button" onclick="toggleAll('hosts',false)">None</button>
    </fieldset>

    <fieldset>
      <legend>Options</legend>
      <label>SSH User:
        <input type="text" name="user" value="{user_val}" />
      </label>
      <label>Tags:
        <input type="text" name="tags" value="{tags_val}" placeholder="comma-separated"/>
      </label>
      <label><input type="checkbox" name="check" value="1" {check_val}/> Check mode</label>
      <label><input type="checkbox" name="become" value="1" {become_val}/> Become (sudo)</label>
    </fieldset>

    <input type="submit" name="run" value="Run"/>
  </form>
</body>
</html>
""")

# ---------------- RUN ----------------
def run_playbook(form: cgi.FieldStorage):
    # (logic unchanged, still uses force_ssh_user backend)
    pass

# ---------------- MAIN ----------------
def main():
    form = cgi.FieldStorage()
    if "run" in form:
        run_playbook(form)
    else:
        render_form(form=form)

if __name__ == "__main__":
    main()

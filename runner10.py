#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ansible Playbook CGI Runner — with forced SSH users and simplified UI.
- Intel always uses clod-user
- AMD always uses cbidd-ada
- SSH user input removed (static info shown instead)
"""

import os, sys, cgi, cgitb, subprocess, datetime, html
cgitb.enable()

# === CONFIG ===
PLAYBOOKS = {
    "intel": {
        "label": "Intel Health Check",
        "path": "/var/www/cgi-bin/intel-check.yml",
        "inventories": ["intel-inv"],
        "force_ssh_user": "clod-user",  # hardcoded
    },
    "amd": {
        "label": "AMD Health Check",
        "path": "/var/www/cgi-bin/amd-check.yml",
        "inventories": ["amd-inv"],
        "force_ssh_user": "cbidd-ada",  # hardcoded
        "ssh_private_key": "/var/lib/www-ansible/keys/serveradmin.pem",
    },
}

INVENTORIES = {
    "intel-inv": {"label": "Intel Inventory", "path": "/etc/ansible/intel_hosts.ini"},
    "amd-inv": {"label": "AMD Inventory", "path": "/etc/ansible/amd_hosts.ini"},
}

REPORT_BASE = "/tmp/healthcheck_reports"
os.makedirs(REPORT_BASE, exist_ok=True)

# === HELPERS ===
def safe(s):
    return html.escape(str(s)) if s else ""

def header_ok():
    print("Content-Type: text/html; charset=utf-8\n")

def run_playbook(playbook_key, inventory_key, hosts, tags, check, become, password, become_pass):
    if playbook_key not in PLAYBOOKS:
        return False, "Unknown playbook key"
    if inventory_key not in INVENTORIES:
        return False, "Unknown inventory key"

    pb_meta = PLAYBOOKS[playbook_key]
    inv_meta = INVENTORIES[inventory_key]

    cmd = [
        "ansible-playbook",
        pb_meta["path"],
        "-i", inv_meta["path"],
    ]

    # forced SSH user
    effective_user = pb_meta.get("force_ssh_user")
    if effective_user:
        cmd += ["-u", effective_user]

    # optional key
    if "ssh_private_key" in pb_meta:
        cmd += ["--private-key", pb_meta["ssh_private_key"]]

    # hosts limit
    if hosts:
        cmd += ["-l", ",".join(hosts)]

    # tags
    if tags:
        cmd += ["--tags", tags]

    if check:
        cmd += ["--check"]
    if become:
        cmd += ["-b"]

    env = os.environ.copy()
    if password:
        env["ANSIBLE_PASSWORD"] = password
    if become_pass:
        env["ANSIBLE_BECOME_PASS"] = become_pass

    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    rpt = os.path.join(REPORT_BASE, f"report-{playbook_key}-{ts}.txt")

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=3600)
        output = proc.stdout + "\n" + proc.stderr
        with open(rpt, "w") as f:
            f.write(output)
        return True, output
    except Exception as e:
        return False, str(e)

def list_reports():
    files = sorted([f for f in os.listdir(REPORT_BASE) if f.startswith("report-")], reverse=True)
    return files

# === INVENTORY PARSER ===
def get_inventory_maps(inv_key):
    groups_map, all_hosts, host_groups = {}, [], {}
    if inv_key not in INVENTORIES:
        return groups_map, all_hosts, host_groups

    path = INVENTORIES[inv_key]["path"]
    if not os.path.exists(path):
        return groups_map, all_hosts, host_groups

    current_group = None
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                current_group = line[1:-1]
                groups_map.setdefault(current_group, [])
            else:
                host = line.split()[0]
                all_hosts.append(host)
                if current_group:
                    groups_map[current_group].append(host)
                    host_groups.setdefault(host, []).append(current_group)
    return groups_map, all_hosts, host_groups

# === FORM RENDER ===
def render_form(msg="", form=None):
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
        ) for k, v in PLAYBOOKS.items()
    )
    inv_opts = "\n".join(
        '<option value="{k}" {sel}>{lbl}</option>'.format(
            k=safe(k), lbl=safe(INVENTORIES[k]["label"]), sel=("selected" if k == inventory_key else "")
        ) for k in allowed_invs if k in INVENTORIES
    )

    if groups_map:
        regions_html = "\n".join(
            '<label><input type="checkbox" name="regions" value="{g}" {chk}/> {g} ({n})</label>'.format(
                g=safe(group), n=len(groups_map[group]), chk=("checked" if group in selected_regions else "")
            ) for group in groups_map
        )
    else:
        regions_html = "<p class='muted'>No regions to show. Select an inventory first.</p>"

    if all_hosts:
        hosts_html = "\n".join(
            '<label><input type="checkbox" name="hosts" value="{h}" data-groups="{gs}" {chk}/> {h}</label>'.format(
                h=safe(h), gs=safe(",".join(host_groups.get(h, []))),
                chk=("checked" if posted_hosts and h in posted_hosts else "")
            ) for h in all_hosts
        )
    else:
        hosts_html = "<p class='muted'>No hosts to show.</p>"

    if selected_playbook in PLAYBOOKS and "force_ssh_user" in PLAYBOOKS[selected_playbook]:
        ssh_user_info = "<p class='muted'>This playbook always uses SSH user: <strong>{}</strong></p>".format(
            safe(PLAYBOOKS[selected_playbook]["force_ssh_user"])
        )
    else:
        ssh_user_info = "<p class='muted'>Pick a playbook to see SSH user.</p>"

    tags_val   = safe(form.getfirst("tags", ""))
    check_val  = "checked" if form.getfirst("check") else ""
    become_val = "checked" if (form.getfirst("become") or not form) else ""
    msg_html   = ("<div class='warn'>{}</div>".format(safe(msg))) if msg else ""

    print(f"""<!DOCTYPE html>
<html><head><meta charset='utf-8'/><title>Ansible Runner</title></head>
<body><h1>Ansible Playbook CGI Runner</h1>
{msg_html}
<form method='post'>
  <input type='hidden' name='action' id='action' value='refresh' />

  <label>Playbook</label>
  <select name='playbook' onchange='this.form.submit()'>
    <option value='' {'selected' if not selected_playbook else ''}>Select a playbook…</option>
    {playbook_opts}
  </select>

  <label>Inventory</label>
  <select name='inventory_key' onchange='this.form.submit()'>
    <option value=''>Pick a playbook first</option>
    {inv_opts}
  </select>

  <label>Regions:</label>{regions_html}
  <label>Hosts:</label>{hosts_html}

  <label>SSH user</label>{ssh_user_info}

  <label>--tags</label><input name='tags' type='text' value='{tags_val}' />

  <label>Password</label><input name='password' type='password' />
  <label>Become password</label><input name='become_pass' type='password' />

  <label><input type='checkbox' name='check' value='1' {check_val}/> Dry run</label>
  <label><input type='checkbox' name='become' value='1' {become_val}/> Become</label>

  <button type='submit' onclick="document.getElementById('action').value='run'">Run</button>
  <a href='?action=list_reports' target='_blank'>Browse reports</a>
</form></body></html>""")

# === MAIN ===
def main():
    form = cgi.FieldStorage()
    action = form.getfirst("action", "")

    if action == "run":
        pb = form.getfirst("playbook")
        inv = form.getfirst("inventory_key")
        hosts = form.getlist("hosts")
        tags = form.getfirst("tags")
        check = form.getfirst("check")
        become = form.getfirst("become")
        pw = form.getfirst("password")
        bpw = form.getfirst("become_pass")

        ok, out = run_playbook(pb, inv, hosts, tags, check, become, pw, bpw)
        header_ok()
        print("<pre>" + safe(out) + "</pre>")

    elif action == "list_reports":
        files = list_reports()
        header_ok()
        print("<h1>Reports</h1><ul>")
        for f in files:
            print(f"<li><a href='{REPORT_BASE}/{f}'>{f}</a></li>")
        print("</ul>")

    else:
        render_form(form=form)

if __name__ == "__main__":
    main()

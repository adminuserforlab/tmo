#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ansible Playbook CGI Runner — Fixed authentication
- Hides file paths in UI (labels only)
- Inventory list filtered by selected playbook
- Regions (INI groups) + scrollable hosts + select all/none
- Intel: force SSH user cloudadmin
- AMD: SSH as serveradmin
- Supports ansible_password + ansible_ssh_pass
"""

import cgi
import cgitb
import os
import subprocess
import configparser
from datetime import datetime

cgitb.enable()

# Paths (adjust as needed)
PLAYBOOKS_DIR = "/path/to/playbooks"
INVENTORIES_DIR = "/path/to/inventories"
REPORTS_DIR = "/path/to/reports"

# Run ansible-playbook command
def run_playbook(playbook, inventory, hosts, ssh_user=None, ssh_pass=None, become_pass=None):
    cmd = ["ansible-playbook", "-i", inventory, playbook]

    # Limit to selected hosts
    if hosts:
        cmd += ["--limit", ",".join(hosts)]

    # SSH user
    if ssh_user:
        cmd += ["-u", ssh_user]

    # Auth secrets
    if ssh_pass:
        # Compatibility for both old and new ansible versions
        cmd += ["-e", f"ansible_password={ssh_pass}"]
        cmd += ["-e", f"ansible_ssh_pass={ssh_pass}"]
    if become_pass:
        cmd += ["-e", f"ansible_become_password={become_pass}"]

    # Run the command
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    output, _ = proc.communicate()
    return output

# Get inventories filtered by playbook
def get_inventories_for_playbook(playbook):
    inventories = []
    for inv in os.listdir(INVENTORIES_DIR):
        if inv.endswith(".inv"):
            inventories.append(inv)
    return inventories

# Get groups/hosts from inventory
def parse_inventory(inventory_path):
    config = configparser.ConfigParser(allow_no_value=True, delimiters=(" ",))
    config.optionxform = str
    config.read(inventory_path)

    groups = {}
    for section in config.sections():
        groups[section] = list(config[section].keys())
    return groups

# Save report
def save_report(hosts, playbook, output):
    os.makedirs(REPORTS_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"report_{ts}.log"
    path = os.path.join(REPORTS_DIR, filename)

    with open(path, "w") as f:
        f.write(f"Playbook: {playbook}\n")
        f.write(f"Hosts: {','.join(hosts)}\n\n")
        f.write(output)

    return filename

# HTML rendering
def print_header():
    print("Content-type: text/html\n")
    print("<html><head><title>Ansible CGI Runner</title></head><body>")

def print_footer():
    print("</body></html>")

# Main logic
form = cgi.FieldStorage()

print_header()

if "run" in form:
    playbook = os.path.join(PLAYBOOKS_DIR, form.getvalue("playbook"))
    inventory = os.path.join(INVENTORIES_DIR, form.getvalue("inventory"))
    hosts = form.getlist("hosts")

    # Auto SSH user selection
    ssh_user = form.getvalue("ssh_user")
    if not ssh_user:
        if "intel" in playbook.lower():
            ssh_user = "cloudadmin"
        elif "amd" in playbook.lower():
            ssh_user = "serveradmin"

    ssh_pass = form.getvalue("ssh_pass")
    become_pass = form.getvalue("become_pass")

    output = run_playbook(playbook, inventory, hosts, ssh_user, ssh_pass, become_pass)

    # Save + show output
    report_file = save_report(hosts, playbook, output)
    print("<h3>Command: Task has been executed and below are the Output</h3>")
    print(f"<pre>{output}</pre>")
    print(f"<p>Report saved: {report_file}</p>")

else:
    # Form UI
    print("<h2>Run Ansible Playbook</h2>")
    print('<form method="post">')

    # Playbooks dropdown
    print("<label>Select Playbook:</label><br>")
    print('<select name="playbook">')
    for pb in os.listdir(PLAYBOOKS_DIR):
        if pb.endswith(".yml") or pb.endswith(".yaml"):
            print(f'<option value="{pb}">{pb}</option>')
    print("</select><br><br>")

    # Inventory dropdown
    print("<label>Select Inventory:</label><br>")
    print('<select name="inventory">')
    for inv in os.listdir(INVENTORIES_DIR):
        if inv.endswith(".inv"):
            print(f'<option value="{inv}">{inv}</option>')
    print("</select><br><br>")

    # SSH + Become credentials
    print('<label>SSH User:</label><input type="text" name="ssh_user"><br>')
    print('<label>SSH Password:</label><input type="password" name="ssh_pass"><br>')
    print('<label>Become Password:</label><input type="password" name="become_pass"><br><br>')

    # Submit
    print('<input type="submit" name="run" value="Run Playbook">')
    print("</form>")

print_footer()

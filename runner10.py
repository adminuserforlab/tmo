#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ansible Playbook CGI Runner — filtered inventories + regions/hosts + output + reports

This variant hard-codes SSH users for playbooks so the UI shows (and -where appropriate-
submits) the forced login user and prevents editing when a playbook requires a fixed user.

- Intel: always login as 'cloud-user'
- AMD:   always login as 'cbidd-ada' (also uses a configured private key)

Other behavior is preserved from the original script (report browsing, masked command,
ANSIBLE env isolation, etc.). Python 3.7+ compatible.
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
# Each playbook can define:
#   force_ssh_user   -> lock SSH login user to this value (overrides form)
#   suggest_ssh_user -> prefill SSH login user (purely cosmetic)
#   ssh_private_key  -> path to key to use for SSH
PLAYBOOKS = {
    "intel": {
        "label": "Intel Health Check",
        "path": "/var/www/cgi-bin/intel-check.yml",
        "inventories": ["intel-inv"],
        # hard-coded user for Intel
        "force_ssh_user": "cloud-user",
    },
    "amd": {
        "label": "AMD Health Check",
        "path": "/var/www/cgi-bin/amd-check.yml",
        "inventories": ["amd-inv"],
        # hard-coded user for AMD
        "force_ssh_user": "cbidd-ada",
        "ssh_private_key": "/var/lib/www-ansible/keys/serveradmin.pem",
        "suggest_ssh_user": "serveradmin",
    },
}

# All known inventories (labels only in UI; paths hidden)
INVENTORIES = {
    "intel-inv": {"label": "Intel Inventory", "path": "/var/www/cgi-bin/intel-inv.ini"},
    "amd-inv":   {"label": "AMD Inventory",   "path": "/var/www/cgi-bin/amd-inv.ini"},
}

# Where your playbooks write HTML reports.
REPORT_BASES = [
    "/var/www/cgi-bin/reports",
]

ANSIBLE_BIN = shutil.which("ansible-playbook") or "/usr/bin/ansible-playbook"
DEFAULT_USER = os.environ.get("ANSIBLE_SSH_USER", "ansadmin")
RUN_TIMEOUT_SECS = 3600
USE_SUDO = False
SUDO_BIN = shutil.which("sudo") or "/usr/bin/sudo"

# Writable HOME/TMP for the web user (apache/www-data)
RUN_HOME = "/var/lib/www-ansible/home"
RUN_TMP  = "/var/lib/www-ansible/tmp"

# Validators
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
    """Parse very simple INI inventory into {group: [hosts]}."""
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
    """From inventory key -> (groups_map, all_hosts_sorted, host->groups map)."""
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

# ---------- Reports ----------
def find_reports(hosts, since_ts, limit=200):
    """Scan REPORT_BASES for .html files modified since since_ts."""
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
                if needles:
                    lo = fn.lower()
                    if not any(n in lo for n in needles):
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
        href = "?action=view_report&b=%d&p=%s" % (bidx, quote(r["rel"]))
        ts   = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["mtime"]))
        items.append('<li><a href="%s" target="_blank">%s — %s</a></li>' % (href, safe(r["rel"]), ts))
    if not items:
        ul = "<p class='muted'>No matching reports found.</p>"
    else:
        ul = "<ul>\n%s\n</ul>" % "\n".join(items)
    return "<h3>%s</h3>%s%s" % (safe(title), ul, ("<p class='muted'>%s</p>" % safe(extra_note) if extra_note else ""))

def serve_report(form):
    """Stream a report HTML file safely."""
    try:
        b = int(form.getfirst("b", "-1"))
    except Exception:
        header_ok()
        print("<pre>Invalid base index.</pre>")
        return
    rel = form.getfirst("p", "")
    if b < 0 or b >= len(REPORT_BASES) or not rel:
        header_ok()
        print("<pre>Invalid parameters.</pre>")
        return

    base = REPORT_BASES[b]
    full = os.path.join(base, rel)
    if not _is_under(base, full) or not os.path.isfile(full):
        header_ok()
        print("<pre>File not found or not allowed.</pre>")
        return

    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            data = f.read()
    except Exception as e:
        header_ok()
        print("<pre>%s</pre>" % safe(str(e)))
        return

    header_ok("text/html; charset=utf-8")
    print(data)

def list_reports_page(form):
    """Simple browser to list recent reports (last 24h by default)."""
    try:
        hours = int(form.getfirst("hours", "24"))
    except Exception:
        hours = 24
    host_filter = (form.getfirst("host", "") or "").strip()
    hosts = [host_filter] if host_filter else []
    since_ts = time.time() - hours * 3600
    reports = find_reports(hosts, since_ts)

    header_ok()
    html_out = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Reports Browser</title>
  <style>
    body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 24px; }}
    .card {{ max-width: 1000px; margin: auto; padding: 20px; border: 1px solid #ddd; border-radius: 12px; box-shadow: 0 2px 6px rgba(0,0,0,.05); }}
    label {{ display:block; margin: 12px 0 6px; font-weight: 600; }}
    input[type=text], select {{ width: 100%; padding: 10px; border: 1px solid #ccc; border-radius: 8px; }}
    .btn, .btn:link, .btn:visited {{
      display:inline-flex; align-items:center; justify-content:center;
      height:48px; padding:0 22px; font-weight:700; font-size:20px; line-height:1;
      color:#fff; background:#0d6efd; border:0; border-radius:16px; text-decoration:none; cursor:pointer;
      box-shadow:0 1px 2px rgba(0,0,0,.06), 0 4px 14px rgba(13,110,253,.25);
      transition:background .15s ease, transform .02s ease; -webkit-appearance:none; appearance:none;
    }}
    button.btn {{ border:0; }}
    .btn:hover {{ background:#0b5ed7; }} .btn:active {{ transform:translateY(1px); }}
    .actions {{ display:flex; gap:16px; margin-top:16px; align-items:center; }}
    .muted {{ color:#666; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>Reports Browser</h1>
    <form method="get" action="">
      <input type="hidden" name="action" value="list_reports" />
      <label for="host">Host contains (optional)</label>
      <input id="host" name="host" type="text" value="{host}" placeholder="e.g. ny1" />
      <label for="hours">Modified within last N hours</label>
      <input id="hours" name="hours" type="text" value="{hours}" />
      <div class="actions">
        <button class="btn" type="submit">Search</button>
        <a class="btn" href="./ansible-runner-cgi.py">Back</a>
      </div>
    </form>
    {list_html}
  </div>
</body>
</html>
""".format(
        host=safe(host_filter),
        hours=hours,
        list_html=render_reports_list("Results", reports, "Showing newest first."),
    )
    print(html_out)

# ---------------- RENDER (FORM) ----------------
def render_form(msg: str = "", form: cgi.FieldStorage = None):
    header_ok()
    if form is None:
        form = cgi.FieldStorage()

    selected_playbook = form.getfirst("playbook", "")
    inventory_key     = form.getfirst("inventory_key", "")
    selected_regions  = form.getlist("regions")
    posted_hosts      = form.getlist("hosts")

    # Filter inventories based on selected playbook
    if selected_playbook in PLAYBOOKS:
        allowed_invs = PLAYBOOKS[selected_playbook]["inventories"]
    else:
        allowed_invs = []

    groups_map, all_hosts, host_groups = get_inventory_maps(inventory_key)

    # Build dropdowns (labels only; hide paths)
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

    # Regions checklist
    if groups_map:
        regions_html = "\n".join(
            '<label><input type="checkbox" name="regions" value="{g}" {chk}/> {g} ({n})</label>'.format(
                g=safe(group), n=len(groups_map[group]), chk=("checked" if group in selected_regions else "")
            )
            for group in groups_map
        )
    else:
        regions_html = "<p class='muted'>No regions to show. Select an inventory first.</p>"

    # Hosts list (scrollable)
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

    # Determine playbook meta to possibly lock SSH user in the form
    pb_meta = PLAYBOOKS.get(selected_playbook, {})
    force_user = pb_meta.get("force_ssh_user")

    # SSH user field value and disabled state
    if force_user:
        # if the playbook forces a user, show the forced user and disable editing
        user_val = safe(force_user)
        user_disabled_attr = 'disabled'
        # include a hidden input so the forced user is submitted (disabled inputs are not sent)
        hidden_user_input = '<input type="hidden" name="user" value="{v}"/>'.format(v=user_val)
        user_note = '<div class="muted">This playbook forces the SSH user: {}</div>'.format(user_val)
    else:
        # suggested user for UX or previously typed value
        if selected_playbook in PLAYBOOKS and PLAYBOOKS[selected_playbook].get("suggest_ssh_user"):
            pref_user = PLAYBOOKS[selected_playbook]["suggest_ssh_user"]
            user_val = safe(form.getfirst("user", pref_user))
        else:
            user_val = safe(form.getfirst("user", DEFAULT_USER))
        user_disabled_attr = ''
        hidden_user_input = ''
        user_note = '<div class="muted">You may change the SSH user here (unless the playbook forces one).</div>'

    tags_val   = safe(form.getfirst("tags", ""))
    check_val  = "checked" if form.getfirst("check") else ""
    become_val = "checked" if (form.getfirst("become") or not form) else ""
    msg_html   = ("<div class='warn'>{}</div>".format(safe(msg))) if msg else ""

    html_out = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Ansible Playbook CGI Runner</title>
  <style>
    body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 24px; }}
    .card {{ max-width: 900px; margin: auto; padding: 20px; border: 1px solid #ddd; border-radius: 12px; box-shadow: 0 2px 6px rgba(0,0,0,.05); }}
    h1 {{ margin-top: 0; }}
    label {{ display:block; margin: 12px 0 6px; font-weight: 700; font-size: 14px; letter-spacing:.2px; }}
    select, input[type=text], input[type=password] {{ width: 100%; padding: 10px; border: 1px solid #ccc; border-radius: 8px; font-size:16px; }}
    .row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
    .muted {{ color: #666; font-size: 0.95em; }}
    .warn {{ background: #fff3cd; border: 1px solid #ffeeba; padding: 8px 12px; border-radius: 8px; }}
    .group-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); grid-gap: 8px; }}
    .hosts-box {{ max-height: 260px; overflow-y: auto; padding: 8px; border: 1px solid #eee; border-radius: 8px; background:#fff; }}
    .toolbar {{ display:flex; gap:8px; margin: 6px 0 10px; }}
    .tbtn {{ padding:6px 10px; border:1px solid #ccc; border-radius:6px; background:#f8f9fa; cursor:pointer; }}
    pre {{ background: #0b1020; color: #d1e7ff; padding: 12px; border-radius: 8px; overflow-x: auto; white-space: pre-wrap; }}

    /* Unified buttons */
    .actions {{ display:flex; gap:16px; margin-top:16px; align-items:center; }}
    .btn, .btn:link, .btn:visited {{
      display:inline-flex; align-items:center; justify-content:center;
      height:48px; padding:0 22px; font-weight:700; font-size:20px; line-height:1;
      color:#fff; background:#0d6efd; border:0; border-radius:16px; text-decoration:none; cursor:pointer;
      box-shadow:0 1px 2px rgba(0,0,0,.06), 0 4px 14px rgba(13,110,253,.25);
      transition:background .15s ease, transform .02s ease; -webkit-appearance:none; appearance:none;
    }}
    button.btn {{ border:0; }}
    .btn:hover {{ background:#0b5ed7; }}
    .btn:active {{ transform:translateY(1px); }}
    .btn:focus {{ outline:none; box-shadow:0 0 0 4px rgba(13,110,253,.25); }}
  </style>
  <script>
    function selectAllHosts(val) {{
      var boxes = document.querySelectorAll('input[name="hosts"]');
      for (var i=0; i<boxes.length; i++) {{ boxes[i].checked = val; }}
    }}
    function toggleInventorySubmit() {{
      document.getElementById('action').value = 'refresh';
      document.getElementById('runnerForm').submit();
    }}
    function onPlaybookChanged() {{
      // When playbook changes, refresh to re-filter inventories and suggested/forced user
      document.getElementById('action').value = 'refresh';
      document.getElementById('runnerForm').submit();
    }}
    function syncRegionToHosts() {{
      var selected = new Set();
      var r = document.querySelectorAll('input[name="regions"]:checked');
      for (var i=0;i<r.length;i++) selected.add(r[i].value);
      var hosts = document.querySelectorAll('input[name="hosts"]');
      for (var j=0;j<hosts.length;j++) {{
        var cb = hosts[j];
        var groups = (cb.getAttribute('data-groups') || '').split(',');
        var match = false;
        for (var k=0;k<groups.length;k++) {{
          if (selected.has(groups[k])) {{ match = true; break; }}
        }}
        if (selected.size > 0) {{
          cb.checked = match;
        }}
      }}
    }}
    document.addEventListener('DOMContentLoaded', function() {{
      var regionCbs = document.querySelectorAll('input[name="regions"]');
      for (var i=0;i<regionCbs.length;i++) {{
        regionCbs[i].addEventListener('change', syncRegionToHosts);
      }}
      syncRegionToHosts();
    }});
  </script>
</head>
<body>
  <div class="card">
    <h1>Ansible Playbook CGI Runner</h1>
    {msg_html}
    <form id="runnerForm" method="post" action="">
      <input type="hidden" name="action" id="action" value="refresh" />

      <label for="playbook">Playbook</label>
      <select id="playbook" name="playbook" required onchange="onPlaybookChanged()">
        <option value="" {sel_pb}>Select a playbook…</option>
        {playbook_opts}
      </select>

      <label for="inventory_key">Inventory</label>
      <select id="inventory_key" name="inventory_key" onchange="toggleInventorySubmit()">
        <option value="">(Pick a playbook first)</option>
        {inv_opts}
      </select>
      <div class="muted">Pick an inventory, then choose regions and/or adjust hosts below.</div>

      <label>Regions (groups) in inventory:</label>
      <div class="group-grid">
        {regions_html}
      </div>
      <div class="toolbar">
        <button type="button" class="tbtn" onclick="selectAllHosts(true)">Select all hosts</button>
        <button type="button" class="tbtn" onclick="selectAllHosts(false)">Select none</button>
      </div>

      <label>Hosts (from selected inventory):</label>
      <div class="hosts-box">
        {hosts_html}
      </div>

      <div class="row">
        <div>
          <label for="user">SSH user (form)</label>
          <input id="user" name="user" type="text" value="{user_val}" {user_disabled} />
          {hidden_user_input}
          {user_note}
        </div>
        <div>
          <label for="tags">--tags (optional, comma-separated)</label>
          <input id="tags" name="tags" type="text" value="{tags_val}" placeholder="setup,deploy" />
        </div>
      </div>

      <label for="password">SSH password</label>
      <input id="password" name="password" type="password" />
      <div class="muted">Ignored for AMD SSH (key-based as forced user). Used for Intel only if needed.</div>

      <label for="become_pass">Become password (optional)</label>
      <input id="become_pass" name="become_pass" type="password" />

      <label><input type="checkbox" name="check" value="1" {check_val}/> Dry run (--check)</label>
      <label><input type="checkbox" name="become" value="1" {become_val}/> Become (-b)</label>

      <div class="actions">
        <button class="btn" type="submit" onclick="document.getElementById('action').value='run'">Run Playbook</button>
        <a class="btn" href="?action=list_reports" target="_blank">Browse reports</a>
      </div>
    </form>
  </div>
</body>
</html>
""".format(
        msg_html=msg_html,
        sel_pb=("selected" if not selected_playbook else ""),
        playbook_opts=playbook_opts,
        inv_opts=inv_opts,
        regions_html=regions_html,
        hosts_html=hosts_html,
        user_val=user_val,
        user_disabled=user_disabled_attr,
        hidden_user_input=hidden_user_input,
        user_note=user_note,
        tags_val=tags_val,
        check_val=check_val,
        become_val=become_val,
    )
    print(html_out)

# ---------------- RUN ----------------
def run_playbook(form: cgi.FieldStorage):
    playbook_key = form.getfirst("playbook", "")
    inventory_key = form.getfirst("inventory_key", "")
    hosts = form.getlist("hosts")
    user  = (form.getfirst("user") or DEFAULT_USER).strip()
    tags  = (form.getfirst("tags") or "").strip()
    do_check  = (form.getfirst("check") == "1")
    do_become = (form.getfirst("become") == "1")
    ssh_pass    = (form.getfirst("password") or "").strip()
    become_pass = (form.getfirst("become_pass") or "").strip()

    # Validation
    if playbook_key not in PLAYBOOKS:
        render_form("Invalid playbook selected.", form); return
    if inventory_key not in INVENTORIES or inventory_key not in PLAYBOOKS[playbook_key]["inventories"]:
        render_form("Invalid inventory for selected playbook.", form); return
    if not hosts:
        render_form("No hosts selected.", form); return
    for h in hosts:
        if not HOST_RE.match(h):
            render_form("Invalid hostname: {}".format(h), form); return
    if not USER_RE.match(user):
        render_form("Invalid SSH user.", form); return
    if tags and not TAGS_RE.match(tags):
        render_form("Invalid characters in tags.", form); return

    playbook_path  = PLAYBOOKS[playbook_key]["path"]
    inventory_path = INVENTORIES[inventory_key]["path"]

    # Effective SSH user + optional key from playbook config
    pb_meta = PLAYBOOKS.get(playbook_key, {})
    effective_ssh_user = pb_meta.get("force_ssh_user") or user
    ssh_private_key    = pb_meta.get("ssh_private_key")

    # AMD: ensure password is not used for SSH when key-based
    if playbook_key == "amd":
        ssh_pass = ""

    # Ensure base dirs exist
    Path(RUN_HOME).mkdir(parents=True, exist_ok=True)
    Path(RUN_TMP).mkdir(parents=True, exist_ok=True)
    local_tmp = os.path.join(RUN_TMP, "ansible-local")
    Path(local_tmp).mkdir(parents=True, exist_ok=True)

    cmd = [
        ANSIBLE_BIN, "-i", inventory_path, playbook_path,
        "--limit", ",".join(hosts),
        "-u", effective_ssh_user
    ]
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
    if ssh_private_key:
        cmd += ["--private-key", ssh_private_key]
    if USE_SUDO:
        cmd = [SUDO_BIN, "-n", "--"] + cmd

    # Environment for ansible
    env = os.environ.copy()
    env["LANG"] = "C.UTF-8"
    env["HOME"] = RUN_HOME
    env["TMPDIR"] = RUN_TMP
    env["ANSIBLE_LOCAL_TEMP"] = local_tmp
    env["ANSIBLE_REMOTE_TMP"] = "/tmp"
    env["ANSIBLE_HOST_KEY_CHECKING"] = "False"
    env["ANSIBLE_SSH_ARGS"] = "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

    TEXT_KW = {"text": True} if sys.version_info >= (3, 7) else {"universal_newlines": True}

    start_ts = time.time()
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
        header_ok(); print("<pre>{}</pre>".format(safe(str(e)))); return

    # Recent reports (last 2 hours or since start)
    since_ts = max(start_ts - 5, time.time() - 2 * 3600)
    recent_reports = find_reports(hosts, since_ts)

    # Render result (mask command)
    header_ok()
    status = "✅ SUCCESS" if rc == 0 else "❌ FAILED (rc={})".format(rc)
    masked_cmd = "ansible-playbook [redacted]"
    recent_html = render_reports_list(
        "Reports (last 2h, matching selected hosts)",
        recent_reports,
        "Roots: {}".format(", ".join(REPORT_BASES)),
    )

    html_out = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Run Result — Ansible Playbook CGI Runner</title>
  <style>
    body {{ font-family: system-ui, Segoe UI, Roboto, Arial, sans-serif; margin: 24px; }}
    .card {{ max-width: 1000px; margin: auto; padding: 20px; border: 1px solid #ddd; border-radius: 12px; box-shadow: 0 2px 6px rgba(0,0,0,.05); }}
    pre {{ background: #0b1020; color: #d1e7ff; padding: 12px; border-radius: 8px; overflow-x: auto; white-space: pre-wrap; }}
    .btn, .btn:link, .btn:visited {{
      display:inline-flex; align-items:center; justify-content:center;
      height:48px; padding:0 22px; font-weight:700; font-size:20px; line-height:1;
      color:#fff; background:#0d6efd; border:0; border-radius:16px; text-decoration:none; cursor:pointer;
      box-shadow:0 1px 2px rgba(0,0,0,.06), 0 4px 14px rgba(13,110,253,.25);
      transition:background .15s ease, transform .02s ease; -webkit-appearance:none; appearance:none;
    }}
    button.btn {{ border:0; }}
    .btn:hover {{ background:#0b5ed7; }} .btn:active {{ transform:translateY(1px); }}
    .actions {{ display:flex; gap:16px; margin-top:16px; align-items:center; }}
    .muted {{ color:#666; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>{status}</h1>
    <p><strong>Command:</strong> <code>{cmd}</code></p>
    <h3>Output</h3>
    <pre>{out}</pre>

    {recent}
    <div class="actions">
      <a class="btn" href="?action=list_reports" target="_blank">Browse reports</a>
      <a class="btn" href="">Run another</a>
    </div>
  </div>
</body>
</html>
""".format(
        status=safe(status),
        cmd=safe(masked_cmd),
        out=safe(output),
        recent=recent_html,
    )
    print(html_out)

# ---------------- MAIN ----------------
def main():
    try:
        method = os.environ.get("REQUEST_METHOD", "GET").upper()
        form = cgi.FieldStorage()
        action = form.getfirst("action", "")
        if method == "GET" and action == "view_report":
            serve_report(form)
        elif method == "GET" and action == "list_reports":
            list_reports_page(form)
        elif method == "POST" and action == "run":
            run_playbook(form)
        else:
            render_form("", form)
    except Exception:
        header_ok()
        import traceback
        print("<pre>{}</pre>".format(safe(traceback.format_exc())))

if __name__ == "__main__":
    main()

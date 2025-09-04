#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Corrected Ansible Playbook CGI Runner
- Cleaned up a few minor logic bugs and tightened validation
- Consistent config keys (suggest_ssh_user)
- Safer handling of subprocess output when text/universal_newlines is used
- Better defaults for form rendering and inventory options
- Python 3.7+ compatible

Notes:
- This file is intended to run under a trusted internal CGI environment.
- Make sure file/dir permissions for RUN_HOME / RUN_TMP / REPORT_BASES allow the web user.
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
        # NOTE: use key name suggest_ssh_user consistently
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
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith(("#", ";")):
                continue
            if line.startswith("[") and line.endswith("]"):
                current = line[1:-1].strip()
                groups.setdefault(current, [])
                continue
            if current:
                token = line.split()[0].split("=")[0].strip()
                if token and token not in groups[current]:
                    groups[current].append(token)
    # remove empty common sections
    for k in ("all", "ungrouped"):
        if k in groups and not groups[k]:
            groups.pop(k, None)
    for k in list(groups.keys()):
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
                if not fn.lower().endswith('.html'):
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
    return f"<h3>{safe(title)}</h3>{ul}{('<p class=\'muted\'>' + safe(extra_note) + '</p>' if extra_note else '')}"

# ---------------- Report serving ----------------

def serve_report(form):
    try:
        b = int(form.getfirst("b", "-1"))
    except Exception:
        header_ok(); print("<pre>Invalid base index.</pre>"); return
    rel = form.getfirst("p", "")
    if b < 0 or b >= len(REPORT_BASES) or not rel:
        header_ok(); print("<pre>Invalid parameters.</pre>"); return
    base = REPORT_BASES[b]
    full = os.path.join(base, rel)
    if not _is_under(base, full) or not os.path.isfile(full):
        header_ok(); print("<pre>File not found or not allowed.</pre>"); return
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            data = f.read()
    except Exception as e:
        header_ok(); print("<pre>%s</pre>" % safe(str(e))); return
    header_ok("text/html; charset=utf-8")
    print(data)


def list_reports_page(form):
    try:
        hours = int(form.getfirst("hours", "24"))
    except Exception:
        hours = 24
    host_filter = (form.getfirst("host", "") or "").strip()
    hosts = [host_filter] if host_filter else []
    since_ts = time.time() - hours * 3600
    reports = find_reports(hosts, since_ts)

    header_ok()
    list_html = render_reports_list("Results", reports, "Showing newest first.")
    html_out = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset=\"utf-8\" />
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
    }}
    .muted {{ color:#666; }}
  </style>
</head>
<body>
  <div class=\"card\">\n    <h1>Reports Browser</h1>
    <form method=\"get\" action=\"\">\n      <input type=\"hidden\" name=\"action\" value=\"list_reports\" />
      <label for=\"host\">Host contains (optional)</label>
      <input id=\"host\" name=\"host\" type=\"text\" value=\"{safe_host}\" placeholder=\"e.g. ny1\" />
      <label for=\"hours\">Modified within last N hours</label>
      <input id=\"hours\" name=\"hours\" type=\"text\" value=\"{hours}\" />
      <div style=\"margin-top:12px;\"><button class=\"btn\" type=\"submit\">Search</button> <a class=\"btn\" href=\"\">Back</a></div>
    </form>
    {list_html}
  </div>
</body>
</html>""".format(safe_host=safe(host_filter), hours=hours, list_html=list_html)
    print(html_out)

# ---------------- Form Renderer ----------------

def render_form(msg: str = "", form: cgi.FieldStorage = None):
    header_ok()
    if form is None:
        form = cgi.FieldStorage()

    selected_playbook = form.getfirst("playbook", "") or ""
    inventory_key     = form.getfirst("inventory_key", "") or ""
    selected_regions  = form.getlist("regions") or []
    posted_hosts      = form.getlist("hosts") or []

    # Allowed inventories for selected playbook
    if selected_playbook in PLAYBOOKS:
        allowed_invs = PLAYBOOKS[selected_playbook].get("inventories", [])
    else:
        allowed_invs = []

    groups_map, all_hosts, host_groups = get_inventory_maps(inventory_key)

    # Playbook options
    playbook_opts = "\n".join(
        '<option value="{k}" {sel}>{lbl}</option>'.format(k=safe(k), lbl=safe(v["label"]), sel=("selected" if k==selected_playbook else ""))
        for k,v in PLAYBOOKS.items()
    )

    # Inventory options (filtered by playbook allowed_invs)
    if allowed_invs:
        inv_opts = "\n".join(
            '<option value="{k}" {sel}>{lbl}</option>'.format(k=safe(k), lbl=safe(INVENTORIES[k]["label"]), sel=("selected" if k==inventory_key else ""))
            for k in allowed_invs if k in INVENTORIES
        )
    else:
        inv_opts = ""

    # Regions checklist
    if groups_map:
        regions_html = "\n".join(
            '<label><input type="checkbox" name="regions" value="{g}" {chk}/> {g} ({n})</label>'.format(
                g=safe(group), n=len(groups_map[group]), chk=("checked" if group in selected_regions else "")
            ) for group in groups_map
        )
    else:
        regions_html = "<p class='muted'>No regions to show. Select an inventory first.</p>"

    # Hosts list
    if all_hosts:
        hosts_html = "\n".join(
            '<label><input type="checkbox" name="hosts" value="{h}" data-groups="{gs}" {chk}/> {h}</label>'.format(
                h=safe(h), gs=safe(",".join(host_groups.get(h, []))), chk=("checked" if posted_hosts and h in posted_hosts else "")
            ) for h in all_hosts
        )
    else:
        hosts_html = "<p class='muted'>No hosts to show.</p>"

    # SSH user field behavior
    forced_user   = PLAYBOOKS.get(selected_playbook, {}).get("force_ssh_user")
    suggest_user  = PLAYBOOKS.get(selected_playbook, {}).get("suggest_ssh_user")

    # Decide preset: suggested -> user param -> DEFAULT_USER
    preset = suggest_user if suggest_user else (form.getfirst("user") or DEFAULT_USER)

    if forced_user:
        user_input_html = (
            '<input id="user_display" name="user_display" type="text" value="{v}" disabled />'
            '<input type="hidden" name="user" value="{v}" />'
            '<div class="muted">SSH login is forced to <strong>{v}</strong> for this playbook.</div>'
        ).format(v=safe(forced_user))
    else:
        preset_is_common = preset in COMMON_USERS
        opts_html = "\n".join(
            '<option value="{u}" {sel}>{u}</option>'.format(u=safe(u), sel=("selected" if (preset_is_common and u==preset) else ""))
            for u in COMMON_USERS
        )
        custom_val = "" if preset_is_common else safe(preset)
        custom_display = "block" if not preset_is_common else "none"
        user_input_html = f"""
        <select name='user' id='user_select'>
          {opts_html}
          <option value='custom' {'selected' if not preset_is_common else ''}>Custom...</option>
        </select>
        <input id='user_custom' name='user_custom' type='text' placeholder='Enter custom SSH user' style='display:{custom_display};margin-top:6px;' value='{custom_val}' />
        <script>
          (function(){{
            var sel = document.getElementById('user_select');
            var inp = document.getElementById('user_custom');
            function toggle() {{ inp.style.display = (sel.value === 'custom') ? 'block' : 'none'; }}
            sel.addEventListener('change', toggle);
            toggle();
          }})();
        </script>
        """

    tags_val   = safe(form.getfirst("tags", ""))
    check_val  = "checked" if form.getfirst("check") else ""
    become_val = "checked" if (form.getfirst("become") or not form) else ""
    msg_html   = ("<div class='warn'>{}</div>".format(safe(msg))) if msg else ""

    html_out = """<!DOCTYPE html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <title>Ansible Playbook CGI Runner</title>
  <style>
    body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 24px; }}
    .card {{ max-width: 900px; margin: auto; padding: 20px; border: 1px solid #ddd; border-radius: 12px; box-shadow: 0 2px 6px rgba(0,0,0,.05); }}
    h1 {{ margin-top: 0; }}
    label {{ display:block; margin: 12px 0 6px; font-weight: 700; font-size: 14px; }}
    select, input[type=text], input[type=password] {{ width: 100%; padding: 10px; border: 1px solid #ccc; border-radius: 8px; font-size:16px; }}
    .row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
    .muted {{ color: #666; font-size: 0.95em; }}
    .warn {{ background: #fff3cd; border: 1px solid #ffeeba; padding: 8px 12px; border-radius: 8px; }}
    .group-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); grid-gap: 8px; }}
    .hosts-box {{ max-height: 260px; overflow-y: auto; padding: 8px; border: 1px solid #eee; border-radius: 8px; background:#fff; }}
    .toolbar {{ display:flex; gap:8px; margin: 6px 0 10px; }}
    .tbtn {{ padding:6px 10px; border:1px solid #ccc; border-radius:6px; background:#f8f9fa; cursor:pointer; }}
    pre {{ background: #0b1020; color: #d1e7ff; padding: 12px; border-radius: 8px; overflow-x: auto; white-space: pre-wrap; }}
    .actions {{ display:flex; gap:16px; margin-top:16px; align-items:center; }}
    .btn {{ display:inline-flex; align-items:center; justify-content:center; height:44px; padding:0 18px; font-weight:700; color:#fff; background:#0d6efd; border-radius:12px; text-decoration:none; border:0; cursor:pointer; }}
  </style>
  <script>
    function selectAllHosts(val) {{
      var boxes = document.querySelectorAll('input[name="hosts"]');
      for (var i=0; i<boxes.length; i++) {{ boxes[i].checked = val; }}
    }}
    function setActionAndSubmit(a) {{
      document.getElementById('action').value = a;
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
        for (var k=0;k<groups.length;k++) {{ if (selected.has(groups[k])) {{ match = true; break; }} }}
        if (selected.size > 0) {{ cb.checked = match; }}
      }}
    }}
    document.addEventListener('DOMContentLoaded', function() {{
      var regionCbs = document.querySelectorAll('input[name="regions"]');
      for (var i=0;i<regionCbs.length;i++) {{ regionCbs[i].addEventListener('change', syncRegionToHosts); }}
      syncRegionToHosts();
    }});
  </script>
</head>
<body>
  <div class=\"card\">
    <h1>Ansible Playbook CGI Runner</h1>
    {msg_html}
    <form id=\"runnerForm\" method=\"post\" action=\"\">
      <input type=\"hidden\" name=\"action\" id=\"action\" value=\"refresh\" />

      <label for=\"playbook\">Playbook</label>
      <select id=\"playbook\" name=\"playbook\" required onchange=\"setActionAndSubmit('refresh')\">
        <option value=\"\" {sel_pb}>Select a playbook…</option>
        {playbook_opts}
      </select>

      <label for=\"inventory_key\">Inventory</label>
      <select id=\"inventory_key\" name=\"inventory_key\" onchange=\"setActionAndSubmit('refresh')\">
        <option value=\"\">(Pick a playbook first)</option>
        {inv_opts}
      </select>
      <div class=\"muted\">Pick an inventory, then choose regions and/or adjust hosts below.</div>

      <label>Regions (groups) in inventory:</label>
      <div class=\"group-grid\">{regions_html}</div>
      <div class=\"toolbar\">
        <button type=\"button\" class=\"tbtn\" onclick=\"selectAllHosts(true)\">Select all hosts</button>
        <button type=\"button\" class=\"tbtn\" onclick=\"selectAllHosts(false)\">Select none</button>
      </div>

      <label>Hosts (from selected inventory):</label>
      <div class=\"hosts-box\">{hosts_html}</div>

      <div class=\"row\">
        <div>
          <label for=\"user\">SSH user (-u)</label>
          {user_input_html}
        </div>
        <div>
          <label for=\"tags\">--tags (optional, comma-separated)</label>
          <input id=\"tags\" name=\"tags\" type=\"text\" value=\"{tags_val}\" placeholder=\"setup,deploy\" />
        </div>
      </div>

      <label for=\"password\">SSH password</label>
      <input id=\"password\" name=\"password\" type=\"password\" />

      <label for=\"become_pass\">Become password (optional)</label>
      <input id=\"become_pass\" name=\"become_pass\" type=\"password\" />

      <label><input type=\"checkbox\" name=\"check\" value=\"1\" {check_val}/> Dry run (--check)</label>
      <label><input type=\"checkbox\" name=\"become\" value=\"1\" {become_val}/> Become (-b)</label>

      <div class=\"actions\">
        <button class=\"btn\" type=\"submit\" onclick=\"document.getElementById('action').value='run'\">Run Playbook</button>
        <a class=\"btn\" href=\"?action=list_reports\" target=\"_blank\">Browse reports</a>
      </div>
    </form>
  </div>
</body>
</html>""".format(
        msg_html=msg_html,
        sel_pb=("selected" if not selected_playbook else ""),
        playbook_opts=playbook_opts,
        inv_opts=inv_opts,
        regions_html=regions_html,
        hosts_html=hosts_html,
        user_input_html=user_input_html,
        tags_val=tags_val,
        check_val=check_val,
        become_val=become_val,
    )

    print(html_out)

# ---------------- RUN ----------------

def run_playbook(form: cgi.FieldStorage):
    playbook_key = form.getfirst("playbook", "") or ""
    inventory_key = form.getfirst("inventory_key", "") or ""
    hosts = form.getlist("hosts") or []

    # user selection: either common user select or custom input
    raw_user = form.getfirst("user") or ""
    if raw_user == "custom":
        user = (form.getfirst("user_custom") or "").strip()
    else:
        user = raw_user.strip() or DEFAULT_USER

    tags  = (form.getfirst("tags") or "").strip()
    do_check  = (form.getfirst("check") == "1")
    do_become = (form.getfirst("become") == "1")
    ssh_pass    = (form.getfirst("password") or "").strip()
    become_pass = (form.getfirst("become_pass") or "").strip()

    # Validation
    if playbook_key not in PLAYBOOKS:
        render_form("Invalid playbook selected.", form); return
    if inventory_key not in INVENTORIES or inventory_key not in PLAYBOOKS[playbook_key].get("inventories", []):
        render_form("Invalid inventory for selected playbook.", form); return
    if not hosts:
        render_form("No hosts selected.", form); return
    for h in hosts:
        if not HOST_RE.match(h):
            render_form("Invalid hostname: {}".format(h), form); return
    if tags and not TAGS_RE.match(tags):
        render_form("Invalid characters in tags.", form); return

    pb_meta = PLAYBOOKS[playbook_key]
    forced_user  = pb_meta.get("force_ssh_user")
    suggest_user = pb_meta.get("suggest_ssh_user")
    become_user  = pb_meta.get("become_user")

    # Decide SSH login user (forced overrides everything)
    if forced_user:
        user = forced_user
    elif suggest_user and (not raw_user):
        user = suggest_user
    if not USER_RE.match(user):
        render_form("Invalid SSH user.", form); return

    playbook_path  = pb_meta["path"]
    inventory_path = INVENTORIES[inventory_key]["path"]

    # Ensure base dirs exist
    Path(RUN_HOME).mkdir(parents=True, exist_ok=True)
    Path(RUN_TMP).mkdir(parents=True, exist_ok=True)
    local_tmp = os.path.join(RUN_TMP, "ansible-local")
    Path(local_tmp).mkdir(parents=True, exist_ok=True)

    # Build command
    cmd = [ANSIBLE_BIN, "-i", inventory_path, playbook_path, "--limit", ",".join(hosts), "-u", user]
    if do_check:
        cmd.append("--check")

    # Playbook policy: if become_user defined, always become that user.
    if become_user:
        cmd += ["-b", "--become-user", become_user]
    elif do_become:
        cmd.append("-b")

    if tags:
        cmd += ["--tags", tags]

    # Auth secrets
    if ssh_pass:
        # pass as extra-vars; note these will appear in process list if not using text mode / sudo masking
        cmd += ["-e", "ansible_password={}".format(ssh_pass), "-e", "ansible_ssh_pass={}".format(ssh_pass)]
    if become_pass:
        cmd += ["-e", "ansible_become_password={}".format(become_pass)]

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
            cwd=str(Path(playbook_path).parent),
            **TEXT_KW
        )
        output = proc.stdout if proc.stdout is not None else ""
        rc = proc.returncode
    except subprocess.TimeoutExpired as e:
        output = e.output if getattr(e, 'output', None) is not None else b""
        if isinstance(output, bytes):
            try:
                output = output.decode('utf-8', errors='replace')
            except Exception:
                output = str(output)
        output = str(output) + "\nERROR: Execution timed out after {}s.\n".format(RUN_TIMEOUT_SECS)
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
  <meta charset=\"utf-8\" />
  <title>Run Result — Ansible Playbook CGI Runner</title>
  <style>
    body {{ font-family: system-ui, Segoe UI, Roboto, Arial, sans-serif; margin: 24px; }}
    .card {{ max-width: 1000px; margin: auto; padding: 20px; border: 1px solid #ddd; border-radius: 12px; box-shadow: 0 2px 6px rgba(0,0,0,.05); }}
    pre {{ background: #0b1020; color: #d1e7ff; padding: 12px; border-radius: 8px; overflow-x: auto; white-space: pre-wrap; }}
    .btn {{ display:inline-flex; align-items:center; justify-content:center; height:44px; padding:0 18px; font-weight:700; color:#fff; background:#0d6efd; border-radius:12px; text-decoration:none; border:0; cursor:pointer; }}
    .actions {{ display:flex; gap:16px; margin-top:16px; align-items:center; }}
    .muted {{ color:#666; }}
  </style>
</head>
<body>
  <div class=\"card\">
    <h1>{status}</h1>
    <p><strong>Command:</strong> <code>{cmd}</code></p>
    <h3>Output</h3>
    <pre>{out}</pre>

    {recent}
    <div class=\"actions\">
      <a class=\"btn\" href=\"?action=list_reports\" target=\"_blank\">Browse reports</a>
      <a class=\"btn\" href=\"\">Run another</a>
    </div>
  </div>
</body>
</html>""".format(
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
        action = form.getfirst("action", "") or ""
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

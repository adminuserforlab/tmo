#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ansible Playbook CGI Runner — Regions + Hosts + Output + Reports
- Regions (INI groups) and multi-host selection
- Region toggles auto-select/clear their hosts
- Scrollable hosts list (compact UI)
- Runs ansible-playbook and shows output inline
- Browse generated HTML reports securely from allowed directories
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
from urllib.parse import quote, unquote

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

# Where your playbooks write HTML reports.
# Put one or more web-accessible roots here.
REPORT_BASES = [
    "/var/www/html/reports",
    # add more roots if you have them
]

ANSIBLE_BIN = shutil.which("ansible-playbook") or "/usr/bin/ansible-playbook"
DEFAULT_USER = os.environ.get("ANSIBLE_SSH_USER", "ansadmin")
RUN_TIMEOUT_SECS = 3600

USE_SUDO = False
SUDO_BIN = shutil.which("sudo") or "/usr/bin/sudo"

RUN_HOME = "/var/lib/www-ansible/home"
RUN_TMP  = "/var/lib/www-ansible/tmp"

# Validators
HOST_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_.,-]+$")
USER_RE  = re.compile(r"^[A-Za-z0-9_.-]+$")
TAGS_RE  = re.compile(r"^[A-Za-z0-9_,.-]+$")


# ---------------- UTIL ----------------
def header_ok(ct="text/html; charset=utf-8"):
    print("Content-Type: " + ct)
    print()


def safe(s: str) -> str:
    return html.escape(s or "")


def parse_ini_inventory_groups(path: str):
    """
    Parse simple INI Ansible inventory with groups/hosts.
    Returns:
      groups: {group_name: [host1, ...]}
    """
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
    # prune empty meta groups if present
    for k in ("all", "ungrouped"):
        if k in groups and not groups[k]:
            groups.pop(k, None)

    # ensure deterministic order
    for k in groups:
        groups[k] = sorted(groups[k])
    return dict(sorted(groups.items(), key=lambda kv: kv[0].lower()))


def get_inventory_maps(inv_key: str):
    """
    From an inventory key -> (groups_map, all_hosts_sorted, host->groups map)
    """
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


# ---------- REPORTS (secure listing/serving) ----------
def _realpath(p: str) -> str:
    return os.path.realpath(p)


def _is_under(base: str, target: str) -> bool:
    base_r = _realpath(base)
    tgt_r  = _realpath(target)
    return tgt_r == base_r or tgt_r.startswith(base_r + os.sep)


def find_reports(hosts, since_ts, limit=200):
    """
    Scan REPORT_BASES for .html files modified since 'since_ts'.
    If hosts list is non-empty, require host substring to match filename.
    Returns list of dicts: {"base": base, "rel": relpath, "path": full, "mtime": mtime}
    """
    out = []
    host_lowers = [h.lower() for h in hosts] if hosts else []
    for base in REPORT_BASES:
        if not os.path.isdir(base):
            continue
        for root, dirs, files in os.walk(base):
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
                if host_lowers:
                    name_lower = fn.lower()
                    if not any(h in name_lower for h in host_lowers):
                        continue
                rel = os.path.relpath(full, base)
                out.append({"base": base, "rel": rel, "path": full, "mtime": st.st_mtime})
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out[:limit]


def render_reports_list(title, reports, extra_note=""):
    # Simple list HTML (used after run and in "Browse all reports")
    items = []
    for r in reports:
        # Encode base index + rel path; never expose absolute paths in URL
        try:
            bidx = REPORT_BASES.index(r["base"])
        except ValueError:
            continue
        href = "?action=view_report&b=%d&p=%s" % (bidx, quote(r["rel"]))
        ts   = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["mtime"]))
        label = "%s — %s" % (r["rel"], ts)
        items.append('<li><a href="%s" target="_blank">%s</a></li>' % (href, safe(label)))

    if not items:
        ul = "<p class='muted'>No matching reports found.</p>"
    else:
        ul = "<ul>\n%s\n</ul>" % ("\n".join(items))

    return """
    <h3>%s</h3>
    %s
    %s
    """ % (safe(title), ul, ("<p class='muted'>%s</p>" % safe(extra_note) if extra_note else ""))


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

    # Stream as text/html (best-effort UTF-8)
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
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 24px; }
    .card { max-width: 1000px; margin: auto; padding: 20px; border: 1px solid #ddd; border-radius: 12px; box-shadow: 0 2px 6px rgba(0,0,0,.05); }
    label { display:block; margin: 12px 0 6px; font-weight: 600; }
    input[type=text], select { width: 100%; padding: 10px; border: 1px solid #ccc; border-radius: 8px; }
    .btn { background: #0d6efd; color: #fff; padding: 8px 14px; border: 0; border-radius: 8px; text-decoration: none; cursor:pointer; }
    .muted { color:#666; }
  </style>
</head>
<body>
  <div class="card">
    <h1>Reports Browser</h1>
    <form method="get" action="">
      <input type="hidden" name="action" value="list_reports" />
      <label for="host">Host contains (optional)</label>
      <input id="host" name="host" type="text" value="%(host)s" placeholder="e.g. ny1" />
      <label for="hours">Modified within last N hours</label>
      <input id="hours" name="hours" type="text" value="%(hours)s" />
      <div style="margin-top:10px;">
        <button class="btn" type="submit">Search</button>
        <a class="btn" href="./new-runner.py">Back</a>
      </div>
    </form>
    %(list_html)s
  </div>
</body>
</html>
""" % {
        "host": safe(host_filter),
        "hours": hours,
        "list_html": render_reports_list("Results", reports, "Showing newest first."),
    }
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

    groups_map, all_hosts, host_groups = get_inventory_maps(inventory_key)

    # Build dropdowns
    playbook_opts = "\n".join(
        '<option value="%s" %s>%s — %s</option>' % (
            safe(k), ("selected" if k == selected_playbook else ""), safe(k), safe(v)
        )
        for k, v in PLAYBOOKS.items()
    )
    inv_opts = "\n".join(
        '<option value="%s" %s>%s — %s</option>' % (
            safe(k), ("selected" if k == inventory_key else ""), safe(k), safe(v)
        )
        for k, v in INVENTORIES.items()
    )

    # Regions checklist
    if groups_map:
        regions_html = "\n".join(
            '<label><input type="checkbox" name="regions" value="%s" %s/> %s (%d)</label>' % (
                safe(group), ("checked" if group in selected_regions else ""), safe(group), len(groups_map[group])
            )
            for group in groups_map
        )
    else:
        regions_html = "<p class='muted'>No regions to show. Select an inventory first.</p>"

    # Hosts list: show ALL hosts
    if all_hosts:
        hosts_html = "\n".join(
            '<label><input type="checkbox" name="hosts" value="%s" data-groups="%s" %s/> %s</label>' % (
                safe(h),
                safe(",".join(host_groups.get(h, []))),
                ("checked" if posted_hosts and h in posted_hosts else ""),
                safe(h),
            )
            for h in all_hosts
        )
    else:
        hosts_html = "<p class='muted'>No hosts to show.</p>"

    user_val   = safe(form.getfirst("user", DEFAULT_USER))
    tags_val   = safe(form.getfirst("tags", ""))
    check_val  = "checked" if form.getfirst("check") else ""
    become_val = "checked" if (form.getfirst("become") or not form) else ""
    msg_html   = ("<div class='warn'>%s</div>" % safe(msg)) if msg else ""

    html_out = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Ansible Playbook CGI Runner</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 24px; }
    .card { max-width: 900px; margin: auto; padding: 20px; border: 1px solid #ddd; border-radius: 12px; box-shadow: 0 2px 6px rgba(0,0,0,.05); }
    h1 { margin-top: 0; }
    label { display:block; margin: 12px 0 6px; font-weight: 600; }
    select, input[type=text], input[type=password] { width: 100%; padding: 10px; border: 1px solid #ccc; border-radius: 8px; }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
    .muted { color: #666; font-size: 0.95em; }
    .btn { background: #0d6efd; color: #fff; padding: 10px 16px; border: 0; border-radius: 8px; cursor: pointer; }
    .warn { background: #fff3cd; border: 1px solid #ffeeba; padding: 8px 12px; border-radius: 8px; }
    .group-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); grid-gap: 8px; }
    .hosts-box { max-height: 260px; overflow-y: auto; padding: 8px; border: 1px solid #eee; border-radius: 8px; background:#fff; }
    .toolbar { display:flex; gap:8px; margin: 6px 0 10px; }
    .tbtn { padding:6px 10px; border:1px solid #ccc; border-radius:6px; background:#f8f9fa; cursor:pointer; }
    pre { background: #0b1020; color: #d1e7ff; padding: 12px; border-radius: 8px; overflow-x: auto; white-space: pre-wrap; }
  </style>
  <script>
    function selectAllHosts(val) {
      var boxes = document.querySelectorAll('input[name="hosts"]');
      for (var i=0; i<boxes.length; i++) { boxes[i].checked = val; }
    }
    function toggleInventorySubmit() {
      document.getElementById('action').value = 'refresh';
      document.getElementById('runnerForm').submit();
    }
    function syncRegionToHosts() {
      var selected = new Set();
      var r = document.querySelectorAll('input[name="regions"]:checked');
      for (var i=0;i<r.length;i++) selected.add(r[i].value);
      var hosts = document.querySelectorAll('input[name="hosts"]');
      for (var j=0;j<hosts.length;j++) {
        var cb = hosts[j];
        var groups = (cb.getAttribute('data-groups') || '').split(',');
        var match = false;
        for (var k=0;k<groups.length;k++) {
          if (selected.has(groups[k])) { match = true; break; }
        }
        if (selected.size > 0) {
          cb.checked = match;   // auto-select hosts of selected regions
        }
      }
    }
    document.addEventListener('DOMContentLoaded', function() {
      var regionCbs = document.querySelectorAll('input[name="regions"]');
      for (var i=0;i<regionCbs.length;i++) {
        regionCbs[i].addEventListener('change', syncRegionToHosts);
      }
      syncRegionToHosts();
    });
  </script>
</head>
<body>
  <div class="card">
    <h1>Ansible Playbook CGI Runner</h1>
    %(msg_html)s
    <form id="runnerForm" method="post" action="">
      <input type="hidden" name="action" id="action" value="refresh" />
      <label for="playbook">Playbook (whitelisted)</label>
      <select id="playbook" name="playbook" required>
        <option value="" %(sel)s>Select a playbook…</option>
        %(playbook_opts)s
      </select>

      <label for="inventory_key">Inventory (whitelisted)</label>
      <select id="inventory_key" name="inventory_key" onchange="toggleInventorySubmit()">
        <option value="">(None — I’ll enter hostnames)</option>
        %(inv_opts)s
      </select>
      <div class="muted">Pick an inventory, then choose regions and/or adjust hosts below.</div>

      <label>Regions (groups) in inventory:</label>
      <div class="group-grid">
        %(regions_html)s
      </div>
      <div class="toolbar">
        <button type="button" class="tbtn" onclick="selectAllHosts(true)">Select all hosts</button>
        <button type="button" class="tbtn" onclick="selectAllHosts(false)">Select none</button>
      </div>

      <label>Hosts (from selected inventory):</label>
      <div class="hosts-box">
        %(hosts_html)s
      </div>

      <div class="row">
        <div>
          <label for="user">SSH user (-u)</label>
          <input id="user" name="user" type="text" value="%(user_val)s" />
        </div>
        <div>
          <label for="tags">--tags (optional, comma-separated)</label>
          <input id="tags" name="tags" type="text" value="%(tags_val)s" placeholder="setup,deploy" />
        </div>
      </div>

      <label for="password">SSH password</label>
      <input id="password" name="password" type="password" />

      <label for="become_pass">Become password (optional)</label>
      <input id="become_pass" name="become_pass" type="password" />

      <label><input type="checkbox" name="check" value="1" %(check_val)s/> Dry run (--check)</label>
      <label><input type="checkbox" name="become" value="1" %(become_val)s/> Become (-b)</label>

      <div style="margin-top:16px;">
        <button class="btn" type="submit" onclick="document.getElementById('action').value='run'">Run Playbook</button>
      </div>
    </form>
  </div>
</body>
</html>
""" % {
        "msg_html": msg_html,
        "sel": ("selected" if not selected_playbook else ""),
        "playbook_opts": playbook_opts,
        "inv_opts": inv_opts,
        "regions_html": regions_html,
        "hosts_html": hosts_html,
        "user_val": user_val,
        "tags_val": tags_val,
        "check_val": check_val,
        "become_val": become_val,
    }
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
            render_form("Invalid hostname: %s" % h, form)
            return
    if not USER_RE.match(user):
        render_form("Invalid SSH user.", form)
        return
    if tags and not TAGS_RE.match(tags):
        render_form("Invalid characters in tags.", form)
        return

    playbook_path  = PLAYBOOKS[playbook_key]
    inventory_path = INVENTORIES[inventory_key]

    # Ensure base dirs exist
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
        cmd += ["-e", "ansible_password=%s" % ssh_pass]
    if become_pass:
        cmd += ["-e", "ansible_become_password=%s" % become_pass]

    if USE_SUDO:
        cmd = [SUDO_BIN, "-n", "--"] + cmd

    # --- OVERRIDE ENV (important)
    env = os.environ.copy()
    env["LANG"] = "C.UTF-8"
    env["HOME"] = RUN_HOME                      # force writable HOME for web user
    env["TMPDIR"] = RUN_TMP                     # force writable TMP
    env["ANSIBLE_LOCAL_TEMP"] = local_tmp       # avoid ~/.ansible/tmp
    env["ANSIBLE_REMOTE_TMP"] = "/tmp"
    env["ANSIBLE_HOST_KEY_CHECKING"] = "False"
    env["ANSIBLE_SSH_ARGS"] = "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

    # Python version–safe text mode
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
        output = (e.output or "") + "\nERROR: Execution timed out after %ss.\n" % RUN_TIMEOUT_SECS
        rc = 124
    except Exception as e:
        header_ok()
        print("<pre>%s</pre>" % safe(str(e)))
        return

    # After run: look for recent reports (last 2 hours or since start)
    since_ts = max(start_ts - 5, time.time() - 2 * 3600)
    recent_reports = find_reports(hosts, since_ts)

    # Render result
    header_ok()
    status = "✅ SUCCESS" if rc == 0 else "❌ FAILED (rc=%d)" % rc
    safe_cmd = " ".join(safe(x) for x in cmd)
    recent_html = render_reports_list(
        "Reports (last 2h, matching selected hosts)",
        recent_reports,
        "Roots: %s" % ", ".join(REPORT_BASES),
    )

    html_out = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Run Result — Ansible Playbook CGI Runner</title>
  <style>
    body { font-family: system-ui, Segoe UI, Roboto, Arial, sans-serif; margin: 24px; }
    .card { max-width: 1000px; margin: auto; padding: 20px; border: 1px solid #ddd; border-radius: 12px; box-shadow: 0 2px 6px rgba(0,0,0,.05); }
    pre { background: #0b1020; color: #d1e7ff; padding: 12px; border-radius: 8px; overflow-x: auto; white-space: pre-wrap; }
    .btn { background: #0d6efd; color: #fff; padding: 8px 14px; border: 0; border-radius: 8px; text-decoration: none; }
    .muted { color:#666; }
  </style>
</head>
<body>
  <div class="card">
    <h1>%(status)s</h1>
    <p><strong>Command:</strong> <code>%(cmd)s</code></p>
    <h3>Output</h3>
    <pre>%(out)s</pre>

    %(recent)s
    <p><a class="btn" href="?action=list_reports" target="_blank">Browse all reports</a></p>
    <p><a class="btn" href="">Run another</a></p>
  </div>
</body>
</html>
""" % {
        "status": status,
        "cmd": safe_cmd,
        "out": safe(output),
        "recent": recent_html,
    }

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
        print("<pre>%s</pre>" % safe(traceback.format_exc()))

if __name__ == "__main__":
    main()

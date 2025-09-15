#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ansible Playbook CGI Runner — full rewrite with report browsing
- Keeps original UI and behaviour
- Adds report listing + safe viewing
- Watch page shows logs + static recent reports (since run start or last 2h)
"""
from __future__ import annotations
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
from urllib.parse import quote, unquote

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

# Where playbooks may deposit HTML reports. Adjust as needed.
REPORT_BASES = ["/tmp"]

ANSIBLE_BIN = shutil.which("ansible-playbook") or "/usr/bin/ansible-playbook"
DEFAULT_USER = os.environ.get("ANSIBLE_SSH_USER", "ansadmin")
RUN_TIMEOUT_SECS = 8 * 3600

USE_SUDO = False
SUDO_BIN = shutil.which("sudo") or "/usr/bin/sudo"

RUN_HOME = "/tmp/www-ansible/home"
RUN_TMP  = "/tmp/www-ansible/tmp"
JOB_DIR  = "/tmp/www-ansible/tmp"

HOST_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
USER_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
TAGS_RE = re.compile(r"^[A-Za-z0-9_,.-]+$")

# ---------------- UTIL ----------------
def header_ok(ct: str = "text/html; charset=utf-8"):
    print("Content-Type: " + ct)
    print()

def safe(s: str) -> str:
    return html.escape("" if s is None else str(s))

def ensure_dirs():
    Path(RUN_HOME).mkdir(parents=True, exist_ok=True)
    Path(RUN_TMP).mkdir(parents=True, exist_ok=True)
    Path(JOB_DIR).mkdir(parents=True, exist_ok=True)

def new_job_id():
    return "%d_%s" % (int(time.time()), os.urandom(5).hex())

def job_paths(job_id: str):
    jdir = os.path.join(JOB_DIR, job_id)
    return {
        "dir": jdir,
        "log": os.path.join(jdir, "output.log"),
        "meta": os.path.join(jdir, "meta.json"),
        "rc": os.path.join(jdir, "rc.txt"),
        "cmd": os.path.join(jdir, "command.txt"),
    }

def write_json(path: str, data):
    with open(path, "w") as f:
        json.dump(data, f)

def read_json(path: str, default=None):
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

# ---------------- INVENTORY PARSING ----------------
def parse_ini_inventory_groups(path: str):
    """Parse simple INI inventory into {group: [hosts]} (best-effort)."""
    groups = {}
    current = None
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8", errors="replace") as f:
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

# ---------------- REPORT HELPERS ----------------
def _is_safe_relpath(rel: str) -> bool:
    # disallow traversal tokens or absolute paths
    if not rel or rel.startswith("/") or ".." in rel:
        return False
    # also disallow backslashes for windows-style traversal
    if "\\" in rel:
        return False
    return True

def find_reports(since_ts: int | None = None, host_filter: str = ""):
    """
    Scan REPORT_BASES for .html reports.
    - since_ts: optional epoch; include files with mtime >= since_ts
    - host_filter: substring to match in filename (case-insensitive)
    Returns a list of dicts: file, path, mtime, base, rel
    """
    results = []
    for base in REPORT_BASES:
        if not os.path.isdir(base):
            continue
        for root, dirs, files in os.walk(base):
            for f in files:
                if not f.lower().endswith(".html"):
                    continue
                path = os.path.join(root, f)
                try:
                    st = os.stat(path)
                except Exception:
                    continue
                if since_ts and st.st_mtime < since_ts:
                    continue
                if host_filter and host_filter.lower() not in f.lower():
                    continue
                rel = os.path.relpath(path, base)
                results.append({
                    "file": f,
                    "path": path,
                    "mtime": int(st.st_mtime),
                    "base": base,
                    "rel": rel
                })
    results.sort(key=lambda r: r["mtime"], reverse=True)
    return results

def render_list_reports(form: cgi.FieldStorage):
    header_ok()
    host_filter = (form.getfirst("host") or "").strip()
    since = int(time.time()) - 24*3600
    reports = find_reports(since_ts=since, host_filter=host_filter)
    print("""<!DOCTYPE html>
<html><head><meta charset="utf-8" />
<title>Reports</title>
<style>
 body { font-family: system-ui, sans-serif; margin: 24px; }
 table { border-collapse: collapse; width: 100%; }
 th, td { border:1px solid #ddd; padding:8px; }
 th { background:#f8f9fa; }
 a { color:#0d6efd; text-decoration:none; }
 a:hover { text-decoration:underline; }
</style></head><body>
<h1>Reports (last 24h)</h1>
<form method="get">
  <input type="hidden" name="action" value="list_reports"/>
  <input type="text" name="host" placeholder="Filter by host substring" value="{filt}"/>
  <button type="submit">Filter</button>
</form>
<table>
<tr><th>Report</th><th>Modified</th><th>Location</th><th>Action</th></tr>
""".format(filt=safe(host_filter)))
    if not reports:
        print("<tr><td colspan=4><em>No reports found.</em></td></tr>")
    for r in reports:
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["mtime"]))
        # link uses base index and rel; since base may contain slashes we round-trip via quote
        link = "?action=view_report&base={}&rel={}".format(quote(r["base"]), quote(r["rel"]))
        print("<tr><td>{}</td><td>{}</td><td>{}</td><td><a href='{}' target='_blank'>View</a></td></tr>".format(
            safe(r["file"]), ts, safe(r["base"]), link))
    print("</table></body></html>")

def render_view_report(form: cgi.FieldStorage):
    # base and rel are expected to have been quoted in list_reports
    base_q = form.getfirst("base") or ""
    rel_q = form.getfirst("rel") or ""
    try:
        base = unquote(base_q)
        rel = unquote(rel_q)
    except Exception:
        header_ok(); print("<pre>Invalid parameters</pre>"); return

    # Validate base is one of REPORT_BASES (exact match)
    if base not in REPORT_BASES:
        header_ok(); print("<pre>Invalid report base</pre>"); return

    if not _is_safe_relpath(rel):
        header_ok(); print("<pre>Invalid report path</pre>"); return

    full = os.path.join(base, rel)
    # Ensure full path is inside base (avoid symlink tricks)
    try:
        base_real = os.path.realpath(base)
        full_real = os.path.realpath(full)
        if not full_real.startswith(base_real + os.sep) and base_real != full_real:
            header_ok(); print("<pre>Access denied</pre>"); return
    except Exception:
        header_ok(); print("<pre>Access validation error</pre>"); return

    if not os.path.isfile(full):
        header_ok(); print("<pre>Report not found</pre>"); return

    # Serve as HTML; the report is likely a full HTML document
    header_ok("text/html; charset=utf-8")
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            sys.stdout.write(f.read())
    except Exception as e:
        header_ok(); print("<pre>Failed to read report: %s</pre>" % safe(str(e)))

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
        '<option value="{k}" {sel}>{lbl}</option>'.format(
            k=safe(k), lbl=safe(v["label"]), sel=("selected" if k == selected_playbook else "")
        )
        for k, v in PLAYBOOKS.items()
    )
    inv_opts = "\n".join(
        '<option value="{k}" {sel}>{lbl}</option>'.format(
            k=safe(k), lbl=safe(INVENTORIES[k]["label"]), sel=("selected" if k == inventory_key else "")
        )
        for k in allowed_invs if k in INVENTORIES
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

    if selected_playbook and "suggest_ssh_user" in PLAYBOOKS[selected_playbook]:
        user_val = safe(PLAYBOOKS[selected_playbook]["suggest_ssh_user"])
    elif selected_playbook and "force_ssh_user" in PLAYBOOKS[selected_playbook]:
        user_val = safe(PLAYBOOKS[selected_playbook]["force_ssh_user"])
    else:
        user_val = safe(DEFAULT_USER)

    tags_val   = safe(form.getfirst("tags", ""))
    check_val  = "checked" if form.getfirst("check") else ""
    become_val = "checked" if (form.getfirst("become") or not form) else ""
    msg_html   = ("<div class='warn'>{}</div>".format(safe(msg))) if msg else ""

    print("""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Ansible Playbook CGI Runner</title>
  <style>
    body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 24px; }}
    .card {{ max-width: 900px; margin: auto; padding: 20px; border: 1px solid #ddd; border-radius: 12px; box-shadow: 0 2px 6px rgba(0,0,0,.05); }}
    h1 {{ margin-top: 0; }}
    label {{ display:block; margin: 12px 0 6px; font-weight: 600; }}
    select, input[type=text], input[type=password] {{ width: 100%; padding: 10px; border: 1px solid #ccc; border-radius: 8px; }}
    .row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
    .muted {{ color: #666; font-size: 0.95em; }}
    .warn {{ background: #fff3cd; border: 1px solid #ffeeba; padding: 8px 12px; border-radius: 8px; }}
    .group-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); grid-gap: 8px; }}
    .hosts-box {{ max-height: 260px; overflow-y: auto; padding: 8px; border: 1px solid #eee; border-radius: 8px; background:#fff; }}
    .toolbar {{ display:flex; gap:8px; margin: 6px 0 10px; }}
    .tbtn {{ padding:6px 10px; border:1px solid #ccc; border-radius:6px; background:#f8f9fa; cursor:pointer; }}
    .actions {{ display:flex; gap:16px; margin-top:16px; align-items:center; }}
    .btn, .btn:link, .btn:visited {{
      display:inline-flex; align-items:center; justify-content:center;
      height:44px; padding:0 18px; font-weight:600; font-size:16px; line-height:1;
      color:#fff; background:#0d6efd; border:0; border-radius:10px; text-decoration:none; cursor:pointer;
      box-shadow:0 1px 2px rgba(0,0,0,.06), 0 4px 14px rgba(13,110,253,.25);
      transition:background .15s ease, transform .02s ease; appearance:none;
    }}
    button.btn {{ border:0; }}
    .btn:hover {{ background:#0b5ed7; }}
    .btn:active {{ transform:translateY(1px); }}
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
        for (var k=0;k<groups.length;k++) {{ if (selected.has(groups[k])) {{ match = true; break; }} }}
        if (selected.size > 0) {{ cb.checked = match; }}
      }}
    }}
    document.addEventListener('DOMContentLoaded', function() {{
      var regionCbs = document.querySelectorAll('input[name="regions"]');
      for (var i=0;i<regionCbs.length;i++) regionCbs[i].addEventListener('change', syncRegionToHosts);
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
          <label for="user">SSH user (-u)</label>
          <input id="user" name="user" type="text" value="{user_val}" />
        </div>
        <div>
          <label for="tags">--tags (optional, comma-separated)</label>
          <input id="tags" name="tags" type="text" value="{tags_val}" placeholder="setup,deploy" />
        </div>
      </div>

      <label><input type="checkbox" name="check" value="1" {check_val}/> Dry run (--check)</label>
      <label><input type="checkbox" name="become" value="1" {become_val}/> Become (-b)</label>

      <label for="password">SSH password (optional)</label>
      <input id="password" name="password" type="password" />
      <label for="become_pass">Become password (optional)</label>
      <input id="become_pass" name="become_pass" type="password" />

      <div class="actions">
        <button class="btn" type="submit" onclick="document.getElementById('action').value='start'">Run Playbook</button>
        <a class="btn" href="?action=list_reports" style="background:#198754; text-decoration:none;">Browse reports</a>
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
    tags_val=tags_val,
    check_val=check_val,
    become_val=become_val,
))

# ---------------- START JOB (background) ----------------
def start_job(form: cgi.FieldStorage):
    playbook_key = form.getfirst("playbook", "")
    inventory_key = form.getfirst("inventory_key", "")
    hosts = form.getlist("hosts")
    user  = (form.getfirst("user") or DEFAULT_USER).strip()
    tags  = (form.getfirst("tags") or "").strip()
    do_check  = (form.getfirst("check") == "1")
    do_become = (form.getfirst("become") == "1")
    ssh_pass    = (form.getfirst("password") or "").strip()
    become_pass = (form.getfirst("become_pass") or "").strip()

    # Validate
    if playbook_key not in PLAYBOOKS:
        render_form("Invalid playbook selected.", form); return
    if inventory_key not in INVENTORIES or inventory_key not in PLAYBOOKS[playbook_key]["inventories"]:
        render_form("Invalid inventory for selected playbook.", form); return
    if not hosts:
        render_form("No hosts selected.", form); return
    for h in hosts:
        if not HOST_RE.match(h): render_form("Invalid hostname: %s" % h, form); return
    if not USER_RE.match(user):
        render_form("Invalid SSH user.", form); return
    if tags and not TAGS_RE.match(tags):
        render_form("Invalid characters in tags.", form); return

    playbook_path  = PLAYBOOKS[playbook_key]["path"]
    inventory_path = INVENTORIES[inventory_key]["path"]

    effective_user = PLAYBOOKS[playbook_key].get("force_ssh_user", user)
    ssh_private_key = PLAYBOOKS[playbook_key].get("ssh_private_key", "")

    ensure_dirs()
    local_tmp = os.path.join(RUN_TMP, "ansible-local")
    Path(local_tmp).mkdir(parents=True, exist_ok=True)

    cmd = [ANSIBLE_BIN, "-i", inventory_path, playbook_path, "--limit", ",".join(hosts), "-u", effective_user]
    if do_check: cmd.append("--check")
    if do_become: cmd.append("-b")
    if tags: cmd += ["--tags", tags]
    if ssh_private_key: cmd += ["--private-key", ssh_private_key]
    if ssh_pass: cmd += ["-e", "ansible_password=%s" % ssh_pass]
    if become_pass: cmd += ["-e", "ansible_become_password=%s" % become_pass]

    if USE_SUDO: cmd = [SUDO_BIN, "-n", "--"] + cmd

    env = os.environ.copy()
    env["LANG"] = "C.UTF-8"
    env["HOME"] = RUN_HOME
    env["TMPDIR"] = RUN_TMP
    env["ANSIBLE_LOCAL_TEMP"] = local_tmp
    env["ANSIBLE_REMOTE_TMP"] = "/tmp"
    env["ANSIBLE_HOST_KEY_CHECKING"] = "False"
    env["ANSIBLE_SSH_ARGS"] = "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
    env["PYTHONUNBUFFERED"] = "1"

    job_id = new_job_id()
    jp = job_paths(job_id)
    Path(jp["dir"]).mkdir(parents=True, exist_ok=True)

    # Save a masked command for debugging (no secrets)
    masked_cmd = " ".join([safe(x) for x in (cmd[:4] + ["[...]" ])])  # very small mask
    with open(jp["cmd"], "w") as f:
        f.write(masked_cmd + "\n")

    meta = {
        "playbook_key": playbook_key,
        "inventory_key": inventory_key,
        "hosts": hosts,
        "user": effective_user,
        "start_ts": int(time.time()),
        "pid": None,
    }
    write_json(jp["meta"], meta)

    logf = open(jp["log"], "w", buffering=1, encoding="utf-8", errors="replace")
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=logf,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=Path(playbook_path).parent
        )
    except Exception as e:
        logf.write("Failed to start process: %s\n" % str(e))
        logf.flush()
        logf.close()
        header_ok(); print("<pre>%s</pre>" % safe(str(e))); return

    meta["pid"] = proc.pid
    write_json(jp["meta"], meta)

    # spawn watcher that writes rc when done (non-blocking approach)
    with open(os.devnull, "wb") as devnull:
        subprocess.Popen(
            ["bash", "-lc", "while kill -0 {pid} 2>/dev/null; do sleep 1; done; echo $? > {rc}".format(pid=proc.pid, rc=quote(jp["rc"]))],
            stdout=devnull, stderr=devnull
        )

    header_ok()
    print("""<!DOCTYPE html>
<html><head><meta http-equiv="refresh" content="0; URL=?action=watch&job=%s"></head>
<body>Starting… <a href="?action=watch&job=%s">Continue</a></body></html>""" % (job_id, job_id))

# ---------------- POLL (tail) ----------------
def poll_job(form: cgi.FieldStorage):
    header_ok("application/json; charset=utf-8")
    job_id = form.getfirst("job", "")
    try:
        pos = int(form.getfirst("pos", "0"))
    except Exception:
        pos = 0
    jp = job_paths(job_id)
    if not os.path.isdir(jp["dir"]):
        print(json.dumps({"error":"no-such-job"})); return

    meta = read_json(jp["meta"], {})
    start_ts = meta.get("start_ts", int(time.time()))
    elapsed = int(time.time() - start_ts)

    append = ""
    try:
        sz = os.path.getsize(jp["log"]) if os.path.exists(jp["log"]) else 0
        if pos < 0: pos = 0
        if sz > pos and os.path.exists(jp["log"]):
            with open(jp["log"], "r", encoding="utf-8", errors="replace") as f:
                f.seek(pos)
                chunk = f.read(128*1024)  # 128KB per poll
                append = chunk
                pos = f.tell()
    except Exception:
        pass

    rc = None
    if os.path.exists(jp["rc"]):
        try:
            with open(jp["rc"], "r") as f:
                rc = int((f.read() or "1").strip())
        except Exception:
            rc = 1
        done = True
    else:
        pid = meta.get("pid")
        done = False if (pid and process_running(int(pid))) else False

    print(json.dumps({"pos": pos, "append": append, "elapsed": elapsed, "done": bool(rc is not None), "rc": rc}))

# ---------------- WATCH PAGE ----------------
def render_watch(form: cgi.FieldStorage):
    job_id = form.getfirst("job", "")
    if not job_id:
        header_ok(); print("<pre>Missing job id.</pre>"); return
    jp = job_paths(job_id)
    if not os.path.isdir(jp["dir"]):
        header_ok(); print("<pre>Unknown job.</pre>"); return

    meta = read_json(jp["meta"], {})
    start_ts = meta.get("start_ts", int(time.time()))

    # Choose window: reports modified since start_ts OR last 2 hours (whichever is earlier => include both),
    # but user asked: "Any reports updated in the last 2 hours (or since start)" — we'll pick since_start_or_2h = min(start_ts, now-2h)
    # For clarity: show reports with mtime >= min(start_ts, now-2h) so if start was within 2h we include since start, otherwise last 2h
    now = int(time.time())
    two_hours_ago = now - 2*3600
    since_ts = start_ts if start_ts >= two_hours_ago else two_hours_ago

    fresh_reports = find_reports(since_ts=since_ts, host_filter="")
    fresh_links = []
    for r in fresh_reports:
        link = "?action=view_report&base={}&rel={}".format(
            quote(r["base"]), quote(r["rel"])
        )
        fresh_links.append("<li><a href='{}' target='_blank'>{}</a> — {}</li>".format(link, safe(r["file"]),
                                                                                   time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["mtime"]))))
    # Render watch page (static fresh reports snapshot included)
    header_ok()
    print("""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Running…</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 24px; }
    .card { max-width: 1000px; margin: auto; padding: 20px; border: 1px solid #ddd; border-radius: 12px; box-shadow: 0 2px 6px rgba(0,0,0,.05); }
    .barwrap { height: 8px; background:#eee; border-radius: 999px; overflow:hidden; margin:12px 0 18px; }
    .bar { width:35%%; height:100%%; background:#0d6efd; animation: indet 1.5s infinite ease-in-out; }
    @keyframes indet { 0%%{transform:translateX(-100%%)} 50%%{transform:translateX(30%%)} 100%%{transform:translateX(100%%)} }
    .spinner { width:18px; height:18px; border:3px solid #0d6efd55; border-top-color:#0d6efd; border-radius:50%%; animation: spin .8s linear infinite; display:inline-block; vertical-align:middle; margin-right:8px; }
    @keyframes spin { to { transform: rotate(360deg); } }
    pre { background:#0b1020; color:#d1e7ff; padding:12px; border-radius:8px; white-space:pre-wrap; max-height:520px; overflow:auto; }
    .muted { color:#666; }
    .actions { display:flex; gap:12px; margin-top:12px; align-items:center; }
    .btn { display:inline-flex; align-items:center; justify-content:center; height:40px; padding:0 16px; font-weight:600; font-size:14px; color:#fff; background:#0d6efd; border:0; border-radius:10px; text-decoration:none; cursor:pointer; }
  </style>
</head>
<body>
  <div class="card">
    <h1 id="title"><span class="spinner"></span>Running…</h1>
    <div class="barwrap"><div class="bar"></div></div>
    <div class="muted" id="elapsed">Elapsed: 0s</div>
    <pre id="log">(connecting…)</pre>
    <div class="actions" id="actions" style="display:none">
      <a class="btn" href="">Run another</a>
      <a class="btn" href="?action=list_reports" target="_blank">Browse reports</a>
    </div>
    <div id="fresh_reports" style="margin-top:16px;">
      <h3>Recent Reports (static snapshot)</h3>
      <ul>
        {fresh}
      </ul>
    </div>
  </div>
<script>
  var job = %s;
  var pos = 0;
  var done = false;
  function poll() {
    if (done) return;
    var xhr = new XMLHttpRequest();
    xhr.open('GET', '?action=poll&job=' + encodeURIComponent(job) + '&pos=' + pos);
    xhr.onreadystatechange = function() {
      if (xhr.readyState === 4 && xhr.status === 200) {
        try {
          var r = JSON.parse(xhr.responseText);
          pos = r.pos;
          document.getElementById('elapsed').textContent = 'Elapsed: ' + r.elapsed + 's';
          if (r.append) {
            var pre = document.getElementById('log');
            pre.textContent += r.append;
            pre.scrollTop = pre.scrollHeight;
          }
          if (r.done) {
            done = true;
            document.getElementById('title').textContent = r.rc === 0 ? '✅ SUCCESS' : ('❌ FAILED (rc=' + r.rc + ')');
            document.querySelector('.barwrap').style.display = 'none';
            document.querySelector('.spinner').style.display = 'none';
            document.getElementById('actions').style.display = 'flex';
          } else {
            setTimeout(poll, 2000);
          }
        } catch (e) {
          setTimeout(poll, 3000);
        }
      } else if (xhr.readyState === 4) {
        setTimeout(poll, 3000);
      }
    };
    xhr.send();
  }
  poll();
</script>
</body></html>
""".format(fresh="\n".join(fresh_links)) % json.dumps(job_id))

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
        elif method == "GET" and action == "list_reports":
            render_list_reports(form)
        elif method == "GET" and action == "view_report":
            render_view_report(form)
        else:
            render_form("", form)
    except Exception:
        header_ok()
        import traceback
        print("<pre>%s</pre>" % safe(traceback.format_exc()))

if __name__ == "__main__":
    main()

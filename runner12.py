#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ansible Playbook CGI Runner (brace-safe) — with Recent HTML Reports
- Old-style % string formatting (no KeyError from CSS braces)
- Run a whitelisted playbook against a whitelisted inventory
- Optional host limit, tags, check mode, become
- Shows playbook output
- Lists recent .html reports (click to open)
- Action ?action=view_report streams report safely
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
# Labels only in UI. Keep real paths here, never shown.
PLAYBOOKS = {
    "intel": { "label": "Intel Health Check", "path": "/var/www/cgi-bin/intel-check.yml" },
    "amd":   { "label": "AMD Health Check",   "path": "/var/www/cgi-bin/amd-check.yml" },
}
INVENTORIES = {
    "intel-inv": { "label": "Intel Inventory", "path": "/var/www/cgi-bin/intel-inv.ini" },
    "amd-inv":   { "label": "AMD Inventory",   "path": "/var/www/cgi-bin/amd-inv.ini"   },
}

# Where playbooks write HTML reports (one or more roots).
REPORT_BASES = [
    "/var/www/cgi-bin/reports"
]

ANSIBLE_BIN = shutil.which("ansible-playbook") or "/usr/bin/ansible-playbook"
DEFAULT_USER = os.environ.get("ANSIBLE_SSH_USER", "ansadmin")
RUN_TIMEOUT_SECS = 3600

# If your web user needs sudo to run ansible:
USE_SUDO = False
SUDO_BIN = shutil.which("sudo") or "/usr/bin/sudo"

# Writable HOME/TMP for the web user (apache/www-data)
RUN_HOME = "/var/lib/www-ansible/home"
RUN_TMP  = "/var/lib/www-ansible/tmp"

USER_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
TAGS_RE = re.compile(r"^[A-Za-z0-9_,.-]+$")
HOST_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

# ---------------- UTIL ----------------
def header_ok(ct="text/html; charset=utf-8"):
    print("Content-Type: " + ct)
    print()

def safe(x):
    return html.escape("" if x is None else str(x))

def _realpath(p):
    return os.path.realpath(p)

def _is_under(base, target):
    base_r = _realpath(base)
    tgt_r  = _realpath(target)
    return tgt_r == base_r or tgt_r.startswith(base_r + os.sep)

# -------- reports helpers --------
def find_reports(hosts, since_ts, limit=200):
    """
    Return newest-first list of dicts:
      { "base": base, "rel": relpath, "path": full, "mtime": mtime }
    Filters by filename containing any of the host tokens (if provided).
    """
    needles = [h.lower() for h in (hosts or []) if h]
    out = []
    for base in REPORT_BASES:
        if not os.path.isdir(base):
            continue
        for root, _dirs, files in os.walk(base):
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

def render_reports_list(title, reports, note=""):
    items = []
    for r in reports:
        try:
            bidx = REPORT_BASES.index(r["base"])
        except ValueError:
            continue
        href = "?action=view_report&b=%d&p=%s" % (bidx, quote(r["rel"]))
        ts   = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["mtime"]))
        items.append('<li><a href="%s" target="_blank">%s</a> — %s</li>' % (href, safe(r["rel"]), safe(ts)))
    if not items:
        ul = "<p class='muted'>No matching reports found.</p>"
    else:
        ul = "<ul>\n%s\n</ul>" % "\n".join(items)
    return "<h3>%s</h3>%s%s" % (safe(title), ul, ("<p class='muted'>%s</p>" % safe(note) if note else ""))

def serve_report(form):
    """GET ?action=view_report&b=<idx>&p=<relpath>"""
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
        header_ok(); print("<pre>%s</pre>" % safe(e)); return
    header_ok("text/html; charset=utf-8")
    print(data)

# ---------------- FORM ----------------
def render_form(msg="", form=None):
    header_ok()
    if form is None:
        form = cgi.FieldStorage()

    selected_playbook = form.getfirst("playbook", "")
    inventory_key     = form.getfirst("inventory_key", "")
    user_val          = form.getfirst("user", DEFAULT_USER) or DEFAULT_USER
    tags_val          = form.getfirst("tags", "") or ""
    hosts_csv         = form.getfirst("hosts", "") or ""
    check_val         = "checked" if form.getfirst("check") else ""
    become_val        = "checked" if form.getfirst("become") else ""

    pb_opts = "\n".join(
        '<option value="%s" %s>%s</option>' %
        (safe(k), ("selected" if k == selected_playbook else ""), safe(v["label"]))
        for k, v in PLAYBOOKS.items()
    )
    inv_opts = "\n".join(
        '<option value="%s" %s>%s</option>' %
        (safe(k), ("selected" if k == inventory_key else ""), safe(v["label"]))
        for k, v in INVENTORIES.items()
    )

    msg_html = ('<div class="warn">%s</div>' % safe(msg)) if msg else ""

    html_tpl = """<!DOCTYPE html>
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
    .warn { background: #fff3cd; border: 1px solid #ffeeba; padding: 8px 12px; border-radius: 8px; }
    .actions { display:flex; gap:16px; margin-top:16px; align-items:center; }
    .btn, .btn:link, .btn:visited {
      display:inline-flex; align-items:center; justify-content:center;
      height:44px; padding:0 18px; font-weight:600; font-size:16px; line-height:1;
      color:#fff; background:#0d6efd; border:0; border-radius:10px; text-decoration:none; cursor:pointer;
      box-shadow:0 1px 2px rgba(0,0,0,.06), 0 4px 14px rgba(13,110,253,.25);
      transition:background .15s ease, transform .02s ease; appearance:none;
    }
    button.btn { border:0; }
    .btn:hover { background:#0b5ed7; }
    .btn:active { transform:translateY(1px); }
    pre { background: #0b1020; color: #d1e7ff; padding: 12px; border-radius: 8px; overflow-x: auto; white-space: pre-wrap; }
  </style>
</head>
<body>
  <div class="card">
    <h1>Ansible Playbook CGI Runner</h1>
    %(msg_html)s
    <form id="runnerForm" method="post" action="">
      <input type="hidden" name="action" id="action" value="run" />

      <label for="playbook">Playbook</label>
      <select id="playbook" name="playbook" required>
        <option value="">Select a playbook…</option>
        %(pb_opts)s
      </select>

      <label for="inventory_key">Inventory</label>
      <select id="inventory_key" name="inventory_key" required>
        <option value="">Select an inventory…</option>
        %(inv_opts)s
      </select>

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

      <label for="hosts">Host limit (--limit), comma-separated (optional)</label>
      <input id="hosts" name="hosts" type="text" value="%(hosts_csv)s" placeholder="host1,host2"/>

      <label><input type="checkbox" name="check" value="1" %(check_val)s/> Dry run (--check)</label>
      <label><input type="checkbox" name="become" value="1" %(become_val)s/> Become (-b)</label>

      <div class="actions">
        <button class="btn" type="submit">Run Playbook</button>
        <a class="btn" href="?action=list_reports" target="_blank">Browse reports</a>
      </div>
    </form>
  </div>
</body>
</html>
"""
    print(html_tpl % {
        "msg_html": msg_html,
        "pb_opts": pb_opts,
        "inv_opts": inv_opts,
        "user_val": safe(user_val),
        "tags_val": safe(tags_val),
        "hosts_csv": safe(hosts_csv),
        "check_val": check_val,
        "become_val": become_val,
    })

# ---------------- RUN ----------------
def run_playbook(form):
    import sys
    playbook_key  = form.getfirst("playbook", "")
    inventory_key = form.getfirst("inventory_key", "")
    ssh_user      = (form.getfirst("user") or DEFAULT_USER).strip()
    tags          = (form.getfirst("tags") or "").strip()
    hosts_csv     = (form.getfirst("hosts") or "").strip()
    do_check      = (form.getfirst("check") == "1")
    do_become     = (form.getfirst("become") == "1")

    # validation
    if playbook_key not in PLAYBOOKS:
        render_form("Invalid playbook selected.", form); return
    if inventory_key not in INVENTORIES:
        render_form("Invalid inventory selected.", form); return
    if not USER_RE.match(ssh_user):
        render_form("Invalid SSH user.", form); return
    if tags and not TAGS_RE.match(tags):
        render_form("Invalid characters in tags.", form); return

    hosts_list = []
    if hosts_csv:
        for t in [x.strip() for x in hosts_csv.split(",") if x.strip()]:
            if not HOST_TOKEN_RE.match(t):
                render_form("Invalid host token: %s" % t, form); return
            hosts_list.append(t)

    playbook_path  = PLAYBOOKS[playbook_key]["path"]
    inventory_path = INVENTORIES[inventory_key]["path"]

    # env & dirs
    Path(RUN_HOME).mkdir(parents=True, exist_ok=True)
    Path(RUN_TMP).mkdir(parents=True, exist_ok=True)
    local_tmp = os.path.join(RUN_TMP, "ansible-local")
    Path(local_tmp).mkdir(parents=True, exist_ok=True)

    cmd = [ANSIBLE_BIN, "-i", inventory_path, playbook_path, "-u", ssh_user]
    if hosts_list:
        cmd += ["--limit", ",".join(hosts_list)]
    if do_check:
        cmd.append("--check")
    if do_become:
        cmd.append("-b")
    if tags:
        cmd += ["--tags", tags]
    if USE_SUDO:
        cmd = [SUDO_BIN, "-n", "--"] + cmd

    env = os.environ.copy()
    env["LANG"] = "C.UTF-8"
    env["HOME"] = RUN_HOME
    env["TMPDIR"] = RUN_TMP
    env["ANSIBLE_LOCAL_TEMP"] = local_tmp
    env["ANSIBLE_REMOTE_TMP"] = "/tmp"
    env["ANSIBLE_HOST_KEY_CHECKING"] = "False"
    env["ANSIBLE_SSH_ARGS"] = "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

    # ---- start streaming page ----
    header_ok()
    head = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Running playbook…</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 24px; }
    .card { max-width: 1000px; margin: auto; padding: 20px; border: 1px solid #ddd; border-radius: 12px; box-shadow: 0 2px 6px rgba(0,0,0,.05); }
    .spinner {
      width:24px;height:24px;border:3px solid #cfe2ff;border-top-color:#0d6efd;border-radius:50%;
      animation:spin 1s linear infinite; display:inline-block; vertical-align:middle; margin-right:8px;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    pre { background:#0b1020; color:#d1e7ff; padding:12px; border-radius:8px; white-space:pre-wrap; }
    .muted { color:#666; }
    .btn { display:inline-block; padding:10px 16px; background:#0d6efd; color:#fff; border-radius:8px; text-decoration:none; }
  </style>
  <script>
    function appendLine(t){var pre=document.getElementById('out'); pre.textContent+=t; window.scrollTo(0,document.body.scrollHeight);}
    function done(){var s=document.getElementById('spin'); if(s) s.style.display='none';}
  </script>
</head>
<body>
  <div class="card">
    <h1><span id="spin" class="spinner"></span>Running playbook…</h1>
    <p class="muted"><strong>Command:</strong> ansible-playbook [redacted]</p>
    <h3>Live output</h3>
    <pre id="out"></pre>
"""
    print(head)
    sys.stdout.flush()

    # run and stream
    start_ts = time.time()
    rc = 0
    try:
        # line-buffered text mode
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=Path(playbook_path).parent,
            bufsize=1,
            universal_newlines=True  # py3.7 compatible text mode
        )
        for line in proc.stdout:
            print("<script>appendLine(%s);</script>" % (repr(line)), end="")
            sys.stdout.flush()
        proc.wait(timeout=RUN_TIMEOUT_SECS)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        print("<script>appendLine(%s);</script>" % repr("\nERROR: Execution timed out.\n"))
        rc = 124
    except Exception as e:
        print("<script>appendLine(%s);</script>" % repr("\nERROR: %s\n" % e))
        rc = 1

    # find recent reports (since start, up to 2h)
    since_ts = max(start_ts - 5, time.time() - 2 * 3600)
    recent = find_reports(hosts_list, since_ts)

    # closing section + reports
    status = "✅ SUCCESS" if rc == 0 else "❌ FAILED (rc=%d)" % rc
    reports_html = render_reports_list(
        "Reports (last ~2h, matching selected hosts)", recent,
        "Roots: %s" % ", ".join(REPORT_BASES)
    )
    tail = """
    <script>done();</script>
    <h3>%s</h3>
    %s
    <p><a class="btn" href="?action=list_reports" target="_blank">Browse reports</a>
       <a class="btn" href="">Run another</a></p>
  </div>
</body>
</html>
""" % (safe(status), reports_html)
    print(tail)
    sys.stdout.flush()
    reports_html = render_reports_list(
        "Reports (last ~2h, matching selected hosts)", recent_reports,
        "Roots: %s" % ", ".join(REPORT_BASES)
    )
    print(html_tpl % {
        "status": safe(status),
        "cmd": safe(masked_cmd),
        "out": safe(output),
        "reports": reports_html,
    })

# ---------------- LIST REPORTS PAGE ----------------
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
    html_tpl = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Reports Browser</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 24px; }
    .card { max-width: 1000px; margin: auto; padding: 20px; border: 1px solid #ddd; border-radius: 12px; box-shadow: 0 2px 6px rgba(0,0,0,.05); }
    label { display:block; margin: 12px 0 6px; font-weight: 600; }
    input[type=text] { width: 100%; padding: 10px; border: 1px solid #ccc; border-radius: 8px; }
    .btn { display:inline-block; padding:10px 16px; background:#0d6efd; color:#fff; border-radius:8px; text-decoration:none; }
    .btn:hover { background:#0b5ed7; }
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
        <a class="btn" href="./%(self)s">Back</a>
      </div>
    </form>
    %(list_html)s
  </div>
</body>
</html>
"""
    print(html_tpl % {
        "host": safe(host_filter),
        "hours": hours,
        "self": os.path.basename(__file__),
        "list_html": render_reports_list("Results", reports, "Newest first."),
    })

# ---------------- MAIN ----------------
def main():
    try:
        form = cgi.FieldStorage()
        action = form.getfirst("action", "")
        if action == "view_report":
            serve_report(form)
        elif action == "list_reports":
            list_reports_page(form)
        elif action == "run":
            run_playbook(form)
        else:
            render_form(form=form)
    except Exception:
        header_ok()
        import traceback
        print("<pre>%s</pre>" % safe(traceback.format_exc()))

if __name__ == "__main__":
    main()

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
from urllib.parse import quote, unquote

cgitb.enable()

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

RUN_HOME = "/tmp/www-ansible/home"
RUN_TMP  = "/tmp/www-ansible/tmp"
JOB_DIR  = "/tmp/www-ansible/tmp"

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
def render_form(error="", form=None):
    header_ok()
    print("""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Ansible Playbook Runner</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 24px; }
    .card { max-width: 900px; margin: auto; padding: 20px; border: 1px solid #ddd; border-radius: 12px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
    .progress { position: relative; width: 100%%; height: 24px; background: #eee; border-radius: 12px; overflow: hidden; margin-top: 16px; }
    .progress-bar { position: absolute; height: 100%%; width: 0%%; background: #0078d7; color: white; text-align: center; line-height: 24px; transition: width 0.4s ease; }
    .logs { background: #111; color: #eee; padding: 12px; border-radius: 8px; margin-top: 16px; height: 300px; overflow: auto; font-family: monospace; font-size: 14px; }
    .btn { display: inline-block; margin-top: 16px; padding: 10px 16px; border-radius: 8px; background: #0078d7; color: #fff; text-decoration: none; }
    .btn:hover { background: #005fa3; }
  </style>
</head>
<body>
  <div class="card">
    <h2>Ansible Playbook Runner</h2>
    <form method="post" action="?action=start">
      <label>Choose Playbook:</label>
      <select name="playbook">
        <option value="intel">Intel Health Check</option>
        <option value="amd">AMD Health Check</option>
      </select><br><br>
      <label>Become:</label>
      <input type="text" name="become" value="%(become_val)s"><br><br>
      <input type="submit" value="Run" class="btn">
    </form>
  </div>
</body>
</html>
""" % {"become_val": form.getfirst("become", "") if form else ""})

# ---------------- WATCH PAGE ----------------
def render_watch(form):
    job_id = form.getfirst("job", "")
    if not job_id:
        header_ok(); print("<pre>Missing job id.</pre>"); return
    jp = job_paths(job_id)
    if not os.path.isdir(jp["dir"]):
        header_ok(); print("<pre>Unknown job.</pre>"); return

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
    .actions { display:flex; gap:12px; margin-top:12px; }
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
      <a class="btn" id="reportBtn" href="#" target="_blank" style="display:none">View HTML Report</a>
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
            if (r.rc === 0 && r.report_url) {
              var btn = document.getElementById('reportBtn');
              btn.href = r.report_url;
              btn.style.display = 'inline-flex';
            }
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
""" % json.dumps(job_id))

# ---------------- POLL ----------------
def poll_job(form):
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
                chunk = f.read(128*1024)
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

    report_url = None
    if rc == 0:
        for base in REPORT_BASES:
            candidate = os.path.join(base, f"{job_id}.html")
            if os.path.exists(candidate):
                report_url = f"/reports/{job_id}.html"
                break

    print(json.dumps({
        "pos": pos,
        "append": append,
        "elapsed": elapsed,
        "done": bool(rc is not None),
        "rc": rc,
        "report_url": report_url
    }))

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

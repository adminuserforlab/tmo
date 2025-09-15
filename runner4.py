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

# Report roots → runner will *scan* these dirs for existing reports
REPORT_BASES = ["/tmp"]

ANSIBLE_BIN = shutil.which("ansible-playbook") or "/usr/bin/ansible-playbook"
DEFAULT_USER = os.environ.get("ANSIBLE_SSH_USER", "ansadmin")
RUN_TIMEOUT_SECS = 8 * 3600
USE_SUDO = False
SUDO_BIN = shutil.which("sudo") or "/usr/bin/sudo"

RUN_HOME = "/tmp/www-ansible/home"
RUN_TMP  = "/tmp/www-ansible/tmp"
JOB_DIR  = "/tmp/www-ansible/tmp"

# ---------------- VALIDATORS ----------------
HOST_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
USER_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
TAGS_RE = re.compile(r"^[A-Za-z0-9_,.-]+$")

# ---------------- UTILS ----------------
def header_ok(ct="text/html; charset=utf-8"):
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

# ---------------- REPORTS ----------------
def find_reports(hosts=None, since_ts=None):
    """Scan REPORT_BASES for .html files, optionally filtering by hosts + recent mtime."""
    results = []
    for base in REPORT_BASES:
        if not os.path.isdir(base):
            continue
        for f in Path(base).rglob("*.html"):
            try:
                st = f.stat()
            except Exception:
                continue
            if since_ts and st.st_mtime < since_ts:
                continue
            if hosts:
                matched = False
                for h in hosts:
                    if h in f.name or h in str(f.parent):
                        matched = True
                        break
                if not matched:
                    continue
            results.append((st.st_mtime, f))
    results.sort(key=lambda x: x[0], reverse=True)
    return results

def render_reports_list(title, reports, roots_note=""):
    if not reports:
        return f"<h2>{safe(title)}</h2><p class='muted'>No reports found. {safe(roots_note)}</p>"
    rows = []
    for ts, f in reports[:20]:
        t = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
        rel = f"{f}"
        rows.append(f"<li><a href='{safe(rel)}' target='_blank'>{safe(f.name)}</a> <span class='muted'>({t})</span></li>")
    return f"<h2>{safe(title)}</h2><ul>{''.join(rows)}</ul><div class='muted'>{safe(roots_note)}</div>"

# ---------------- FORM ----------------
def render_form(msg: str = "", form: cgi.FieldStorage = None):
    header_ok()
    if form is None:
        form = cgi.FieldStorage()

    selected_playbook = form.getfirst("playbook", "")
    inventory_key     = form.getfirst("inventory_key", "")

    playbook_opts = "\n".join(
        f'<option value="{safe(k)}" {"selected" if k == selected_playbook else ""}>{safe(v["label"])}</option>'
        for k, v in PLAYBOOKS.items()
    )
    inv_opts = "\n".join(
        f'<option value="{safe(k)}" {"selected" if k == inventory_key else ""}>{safe(INVENTORIES[k]["label"])}</option>'
        for k in PLAYBOOKS.get(selected_playbook, {}).get("inventories", []) if k in INVENTORIES
    )

    msg_html = f"<div class='warn'>{safe(msg)}</div>" if msg else ""

    print(f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"/><title>Ansible Runner</title></head>
<body>
  <h1>Ansible Playbook CGI Runner</h1>
  {msg_html}
  <form method="post">
    <input type="hidden" name="action" value="start"/>
    <label>Playbook</label>
    <select name="playbook" required>{playbook_opts}</select>
    <label>Inventory</label>
    <select name="inventory_key">{inv_opts}</select>
    <label>Hosts (comma separated)</label>
    <input name="hosts" type="text" placeholder="host1,host2"/>
    <label>User</label>
    <input name="user" type="text" value="{safe(DEFAULT_USER)}"/>
    <div><button type="submit">Run Playbook</button></div>
  </form>
</body></html>""")

# ---------------- START ----------------
def start_job(form: cgi.FieldStorage):
    playbook_key = form.getfirst("playbook", "")
    inventory_key = form.getfirst("inventory_key", "")
    hosts = (form.getfirst("hosts") or "").split(",")
    hosts = [h.strip() for h in hosts if h.strip()]
    user  = (form.getfirst("user") or DEFAULT_USER).strip()

    if playbook_key not in PLAYBOOKS:
        render_form("Invalid playbook.", form); return
    if inventory_key not in INVENTORIES or inventory_key not in PLAYBOOKS[playbook_key]["inventories"]:
        render_form("Invalid inventory.", form); return
    if not hosts:
        render_form("No hosts selected.", form); return
    if not USER_RE.match(user):
        render_form("Invalid SSH user.", form); return

    playbook_path  = PLAYBOOKS[playbook_key]["path"]
    inventory_path = INVENTORIES[inventory_key]["path"]
    effective_user = PLAYBOOKS[playbook_key].get("force_ssh_user", user)

    ensure_dirs()
    job_id = new_job_id()
    jp = job_paths(job_id)
    Path(jp["dir"]).mkdir(parents=True, exist_ok=True)

    cmd = [ANSIBLE_BIN, "-i", inventory_path, playbook_path, "--limit", ",".join(hosts), "-u", effective_user]
    if USE_SUDO: cmd = [SUDO_BIN, "-n", "--"] + cmd

    env = os.environ.copy()
    env["HOME"] = RUN_HOME
    env["TMPDIR"] = RUN_TMP
    env["ANSIBLE_HOST_KEY_CHECKING"] = "False"

    logf = open(jp["log"], "w", buffering=1, encoding="utf-8", errors="replace")
    try:
        proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT, env=env, cwd=Path(playbook_path).parent)
    except Exception as e:
        logf.write("Failed: %s\n" % str(e))
        logf.close()
        header_ok(); print("<pre>%s</pre>" % safe(str(e))); return

    write_json(jp["meta"], {"playbook": playbook_key, "hosts": hosts, "pid": proc.pid, "start_ts": int(time.time())})

    header_ok()
    print(f"""<!DOCTYPE html>
<html><head><meta http-equiv="refresh" content="0; URL=?action=watch&job={job_id}"></head>
<body>Starting…</body></html>""")

# ---------------- WATCH ----------------
def render_watch(form):
    job_id = form.getfirst("job", "")
    if not job_id:
        header_ok(); print("<pre>No job id.</pre>"); return
    jp = job_paths(job_id)
    if not os.path.isdir(jp["dir"]):
        header_ok(); print("<pre>Unknown job.</pre>"); return

    header_ok()
    print(f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"/><title>Running…</title></head>
<body>
  <h1>Running Job {safe(job_id)}</h1>
  <pre id="log">Streaming…</pre>
  <script>
    var pos=0; var done=false;
    function poll() {{
      if(done) return;
      var xhr=new XMLHttpRequest();
      xhr.open('GET','?action=poll&job={job_id}&pos='+pos);
      xhr.onload=function(){{
        try {{
          var r=JSON.parse(xhr.responseText);
          pos=r.pos;
          if(r.append) {{
            var pre=document.getElementById('log');
            pre.textContent+=r.append;
            pre.scrollTop=pre.scrollHeight;
          }}
          if(r.done) {{
            done=true;
            document.title="Done";
          }} else setTimeout(poll,2000);
        }} catch(e){{ setTimeout(poll,3000); }}
      }};
      xhr.send();
    }}; poll();
  </script>
</body></html>""")

# ---------------- POLL ----------------
def poll_job(form):
    header_ok("application/json; charset=utf-8")
    job_id = form.getfirst("job", "")
    try: pos = int(form.getfirst("pos", "0"))
    except: pos=0
    jp = job_paths(job_id)
    if not os.path.isdir(jp["dir"]):
        print(json.dumps({"error":"no-such-job"})); return
    append=""; sz=0
    if os.path.exists(jp["log"]):
        sz=os.path.getsize(jp["log"])
        if sz>pos:
            with open(jp["log"],"r",encoding="utf-8",errors="replace") as f:
                f.seek(pos); chunk=f.read(128*1024); append=chunk; pos=f.tell()
    rc=None; done=False
    if os.path.exists(jp["rc"]):
        try: rc=int(open(jp["rc"]).read().strip() or "1")
        except: rc=1
        done=True
    print(json.dumps({"pos":pos,"append":append,"done":done,"rc":rc}))

# ---------------- MAIN ----------------
def main():
    try:
        method = os.environ.get("REQUEST_METHOD", "GET").upper()
        form = cgi.FieldStorage()
        action = form.getfirst("action", "")
        if method=="POST" and action=="start": start_job(form)
        elif method=="GET" and action=="watch": render_watch(form)
        elif method=="GET" and action=="poll": poll_job(form)
        else: render_form("", form)
    except Exception:
        header_ok(); import traceback; print("<pre>%s</pre>" % safe(traceback.format_exc()))

if __name__=="__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ansible Playbook CGI Runner — Python 3 full working
- Background job execution with live logs
- Polling/watch page
- Browse & view HTML reports safely
- Playbook/inventory/hosts selection UI
"""

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
    "intel": {"label": "Intel Health Check", "path": "/var/www/cgi-bin/intel-check.yml", "inventories": ["intel-inv"], "force_ssh_user": "cloudadmin"},
    "amd":   {"label": "AMD Health Check",   "path": "/var/www/cgi-bin/amd-check.yml",   "inventories": ["amd-inv"]},
}

INVENTORIES = {
    "intel-inv": {"label": "Intel Inventory", "path": "/var/www/cgi-bin/intel-inv.ini"},
    "amd-inv":   {"label": "AMD Inventory",   "path": "/var/www/cgi-bin/amd-inv.ini"},
}

REPORT_BASES = ["/tmp"]
ANSIBLE_BIN = shutil.which("ansible-playbook") or "/usr/bin/ansible-playbook"
DEFAULT_USER = os.environ.get("ANSIBLE_SSH_USER", "ansadmin")
RUN_HOME = "/tmp/www-ansible/home"
RUN_TMP  = "/tmp/www-ansible/tmp"
JOB_DIR  = "/tmp/www-ansible/tmp"

HOST_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
USER_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
TAGS_RE = re.compile(r"^[A-Za-z0-9_,.-]+$")

USE_SUDO = False
SUDO_BIN = shutil.which("sudo") or "/usr/bin/sudo"

# ---------------- UTIL ----------------
def header_ok(content_type="text/html; charset=utf-8"):
    print(f"Content-Type: {content_type}\n")

def safe(s):
    return html.escape("" if s is None else str(s))

def ensure_dirs():
    Path(RUN_HOME).mkdir(parents=True, exist_ok=True)
    Path(RUN_TMP).mkdir(parents=True, exist_ok=True)
    Path(JOB_DIR).mkdir(parents=True, exist_ok=True)

def new_job_id():
    return f"{int(time.time())}_{os.urandom(5).hex()}"

def job_paths(job_id):
    jdir = os.path.join(JOB_DIR, job_id)
    return {"dir": jdir, "log": os.path.join(jdir, "output.log"), "meta": os.path.join(jdir, "meta.json"), "rc": os.path.join(jdir, "rc.txt"), "cmd": os.path.join(jdir, "command.txt")}

def write_json(path, data):
    with open(path, "w") as f: json.dump(data, f)

def read_json(path, default=None):
    try: return json.load(open(path))
    except Exception: return default

def process_running(pid):
    try: os.kill(pid, 0); return True
    except Exception: return False

# ---------------- INVENTORY ----------------
def parse_ini_inventory_groups(path):
    groups, current = {}, None
    if not os.path.exists(path): return {}
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line or line.startswith(("#", ";")): continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
            groups.setdefault(current, [])
            continue
        if current:
            token = line.split()[0].split("=")[0].strip()
            if token and token not in groups[current]: groups[current].append(token)
    for k in ("all", "ungrouped"): groups.pop(k, None)
    for k in groups: groups[k] = sorted(groups[k], key=str.lower)
    return dict(sorted(groups.items(), key=lambda kv: kv[0].lower()))

def get_inventory_maps(inv_key):
    meta = INVENTORIES.get(inv_key or "", {})
    path = meta.get("path", "")
    if not path: return {}, [], {}
    groups_map = parse_ini_inventory_groups(path)
    host_groups = {}
    for g, hosts in groups_map.items():
        for h in hosts: host_groups.setdefault(h, []).append(g)
    return groups_map, sorted(host_groups.keys(), key=str.lower), host_groups

# ---------------- REPORTS ----------------
def _is_safe_relpath(rel):
    return bool(rel and not rel.startswith("/") and ".." not in rel and "\\" not in rel)

def find_reports(since_ts=None, host_filter=""):
    results = []
    for base in REPORT_BASES:
        if not os.path.isdir(base): continue
        for root, dirs, files in os.walk(base):
            for f in files:
                if not f.lower().endswith(".html"): continue
                path = os.path.join(root, f)
                try: st = os.stat(path)
                except Exception: continue
                if since_ts and st.st_mtime < since_ts: continue
                if host_filter and host_filter.lower() not in f.lower(): continue
                rel = os.path.relpath(path, base)
                results.append({"file": f, "path": path, "mtime": int(st.st_mtime), "base": base, "rel": rel})
    return sorted(results, key=lambda r: r["mtime"], reverse=True)

# ---------------- FORM/UI ----------------
def render_form(msg="", form=None):
    header_ok()
    if form is None: form = cgi.FieldStorage()
    print(f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>CGI Runner</title></head><body>
<h1>CGI Runner</h1><p>{safe(msg)}</p>
<form method="post">
Playbook: <select name="playbook">
{''.join(f'<option value="{k}">{v["label"]}</option>' for k,v in PLAYBOOKS.items())}
</select>
<button type="submit" onclick="document.getElementById('action').value='start'">Run Playbook</button>
</form></body></html>""")

# ---------------- START JOB ----------------
def start_job(form):
    playbook_key = form.getfirst("playbook","")
    inventory_key = form.getfirst("inventory_key","")
    hosts = form.getlist("hosts")
    user = form.getfirst("user", DEFAULT_USER)
    tags = form.getfirst("tags","")
    do_check = form.getfirst("check")=="1"
    do_become = form.getfirst("become")=="1"

    if playbook_key not in PLAYBOOKS:
        render_form("Invalid playbook", form); return
    playbook_path = PLAYBOOKS[playbook_key]["path"]
    inventory_path = INVENTORIES.get(inventory_key, {}).get("path", "")
    if not hosts: render_form("No hosts selected", form); return

    ensure_dirs()
    jp = job_paths(new_job_id())
    Path(jp["dir"]).mkdir(parents=True, exist_ok=True)

    cmd = [ANSIBLE_BIN, "-i", inventory_path, playbook_path, "-u", PLAYBOOKS[playbook_key].get("force_ssh_user", user)]
    if do_check: cmd.append("--check")
    if do_become: cmd.append("-b")
    if tags: cmd += ["--tags", tags]

    logf = open(jp["log"], "w", encoding="utf-8", buffering=1)
    proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT, env=os.environ.copy())
    write_json(jp["meta"], {"playbook_key":playbook_key,"hosts":hosts,"pid":proc.pid,"start_ts":int(time.time())})
    header_ok()
    print(f"""<!DOCTYPE html><html><head><meta http-equiv="refresh" content="0; URL=?action=watch&job={jp['dir'].split('/')[-1]}"></head>
<body>Starting… <a href="?action=watch&job={jp['dir'].split('/')[-1]}">Continue</a></body></html>""")

# ---------------- POLL ----------------
def poll_job(form):
    job_id = form.getfirst("job","")
    pos = int(form.getfirst("pos","0") or 0)
    jp = job_paths(job_id)
    append = ""
    if os.path.exists(jp["log"]):
        with open(jp["log"], "r", encoding="utf-8", errors="replace") as f:
            f.seek(pos)
            append = f.read()
            pos = f.tell()
    rc = None
    meta = read_json(jp["meta"], {})
    pid = meta.get("pid")
    if pid and not process_running(pid) and os.path.exists(jp["log"]):
        rc = 0
    print(json.dumps({"pos":pos,"append":append,"elapsed":int(time.time()-meta.get("start_ts",int(time.time()))),"done":bool(rc is not None),"rc":rc}))

# ---------------- WATCH ----------------
def render_watch(form):
    job_id = form.getfirst("job","")
    fresh_reports = find_reports(since_ts=int(time.time())-2*3600)
    fresh_links = [f"<li><a href='?action=view_report&base={quote(r['base'])}&rel={quote(r['rel'])}' target='_blank'>{safe(r['file'])}</a> - {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(r['mtime']))}</li>" for r in fresh_reports]
    header_ok()
    print(f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Watching job</title></head>
<body><h1>Running job {job_id}</h1><pre id="log">(connecting…)</pre>
<h3>Recent reports</h3><ul>{''.join(fresh_links)}</ul>
<script>
var job={json.dumps(job_id)};var pos=0;var done=false;
function poll(){{if(done) return;var xhr=new XMLHttpRequest();xhr.open('GET','?action=poll&job='+encodeURIComponent(job)+'&pos='+pos);xhr.onreadystatechange=function(){{if(xhr.readyState===4 && xhr.status===200){{var r=JSON.parse(xhr.responseText);pos=r.pos;document.getElementById('log').textContent+=r.append;if(r.done) done=true;else setTimeout(poll,2000);}}}};xhr.send();}}
poll();
</script></body></html>""")

# ---------------- REPORT LIST/VIEW ----------------
def render_list_reports(form):
    reports = find_reports()
    header_ok()
    print("<html><body><h1>Reports</h1><ul>")
    for r in reports: print(f"<li><a href='?action=view_report&base={quote(r['base'])}&rel={quote(r['rel'])}' target='_blank'>{safe(r['file'])}</a> - {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(r['mtime']))}</li>")
    print("</ul></body></html>")

def render_view_report(form):
    base = unquote(form.getfirst("base",""))
    rel = unquote(form.getfirst("rel",""))
    if not _is_safe_relpath(rel): header_ok(); print("Unsafe path"); return
    path = os.path.join(base, rel)
    if not os.path.exists(path): header_ok(); print("Report not found"); return
    header_ok("text/html; charset=utf-8")
    print(open(path, "r", encoding="utf-8", errors="replace").read())

# ---------------- MAIN ----------------
def main():
    try:
        method = os.environ.get("REQUEST_METHOD", "GET").upper()
        form = cgi.FieldStorage()
        action = form.getfirst("action","")
        if method=="POST" and action=="start": start_job(form)
        elif method=="GET" and action=="watch": render_watch(form)
        elif method=="GET" and action=="poll": poll_job(form)
        elif method=="GET" and action=="list_reports": render_list_reports(form)
        elif method=="GET" and action=="view_report": render_view_report(form)
        else: render_form("", form)
    except Exception:
        header_ok()
        import traceback
        print("<pre>%s</pre>" % safe(traceback.format_exc()))

if __name__=="__main__":
    main()

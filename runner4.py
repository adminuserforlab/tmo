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
    },
    "nokia": {
        "label": "Nokia Health Check",
        "path": "/var/www/cgi-bin/nokia-check.yml",
    },
}

REPORT_BASES = ["/tmp"]

JOB_DIR = Path("/tmp/www-ansible/tmp")
os.makedirs(JOB_DIR, exist_ok=True)

def header_ok():
    print("Content-Type: text/html; charset=utf-8")
    print()

def header_json():
    print("Content-Type: application/json")
    print()

def job_paths(job_id):
    jdir = JOB_DIR / job_id
    return {
        "dir": str(jdir),
        "stdout": str(jdir / "stdout.txt"),
        "rc": str(jdir / "rc.txt"),
        "start": str(jdir / "start.txt"),
    }

def launch_job(playbook):
    job_id = str(int(time.time() * 1000))
    jdir = JOB_DIR / job_id
    os.makedirs(jdir, exist_ok=True)
    jp = job_paths(job_id)
    with open(jp["start"], "w") as f:
        f.write(str(int(time.time())))
    with open(jp["stdout"], "w") as f:
        pass
    proc = subprocess.Popen(
        ["ansible-playbook", playbook],
        stdout=open(jp["stdout"], "w"),
        stderr=subprocess.STDOUT,
    )
    with open(jp["rc"], "w") as f:
        f.write(str(proc.pid))
    return job_id

def poll_job(job_id, pos):
    jp = job_paths(job_id)
    if not os.path.isdir(jp["dir"]):
        return {"error": "unknown job"}
    try:
        with open(jp["stdout"], "r") as f:
            f.seek(pos)
            append = f.read()
            newpos = f.tell()
    except FileNotFoundError:
        append = ""
        newpos = pos
    rc = None
    done = False
    try:
        with open(jp["rc"], "r") as f:
            val = f.read().strip()
        if val.isdigit():
            pid = int(val)
            if not os.path.exists(f"/proc/{pid}"):
                rc = 0
                done = True
        else:
            rc = int(val)
            done = True
    except FileNotFoundError:
        pass
    elapsed = 0
    try:
        with open(jp["start"], "r") as f:
            start = int(f.read().strip())
            elapsed = int(time.time()) - start
    except:
        pass
    return {"pos": newpos, "append": append, "done": done, "rc": rc, "elapsed": elapsed}

def render_index():
    header_ok()
    print("<html><head><title>Ansible Playbook CGI Runner</title></head><body>")
    print("<h1>Run Playbook</h1><ul>")
    for key, pb in PLAYBOOKS.items():
        print(f'<li><a href="?action=run&playbook={key}">{html.escape(pb["label"])}</a></li>')
    print("</ul></body></html>")

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
      <!-- New report button (hidden until success) -->
      <a class="btn" id="reportLink" href="#" target="_blank" style="display:none">View HTML Report</a>
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
            if (r.rc === 0) {
              // Show link to job-specific report
              document.getElementById('reportLink').href = '/tmp/www-ansible/tmp/' + job + '/report.html';
              document.getElementById('reportLink').style.display = 'inline-flex';
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

def render_list_reports():
    header_ok()
    print("<html><head><title>Reports</title></head><body>")
    print("<h1>Reports</h1><ul>")
    for base in REPORT_BASES:
        if os.path.isdir(base):
            for fn in sorted(os.listdir(base), reverse=True):
                if fn.endswith(".html"):
                    path = os.path.join(base, fn)
                    print(f'<li><a href="{html.escape(path)}">{html.escape(fn)}</a></li>')
    print("</ul></body></html>")

def main():
    form = cgi.FieldStorage()
    action = form.getfirst("action")
    if action == "run":
        pbkey = form.getfirst("playbook")
        if pbkey not in PLAYBOOKS:
            header_ok(); print("<pre>Unknown playbook.</pre>"); return
        job_id = launch_job(PLAYBOOKS[pbkey]["path"])
        header_ok()
        print(f'<html><body><script>location.href="?action=watch&job={job_id}";</script></body></html>')
    elif action == "watch":
        render_watch(form)
    elif action == "poll":
        job_id = form.getfirst("job")
        pos = int(form.getfirst("pos") or 0)
        header_json()
        print(json.dumps(poll_job(job_id, pos)))
    elif action == "list_reports":
        render_list_reports()
    else:
        render_index()

if __name__ == "__main__":
    main()

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

REPORT_BASES = [
    "/var/www/cgi-bin/reports",
    "/tmp"
]

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


# ---------------- REPORT SCAN ----------------
def find_latest_report():
    latest = None
    latest_mtime = 0
    for base in REPORT_BASES:
        if not os.path.isdir(base):
            continue
        for root, _, files in os.walk(base):
            for f in files:
                if f.endswith(".html"):
                    path = os.path.join(root, f)
                    try:
                        mtime = os.path.getmtime(path)
                        if mtime > latest_mtime:
                            latest_mtime = mtime
                            latest = path
                    except Exception:
                        pass
    return latest


# ---------------- START JOB ----------------
def start_job(form: cgi.FieldStorage):
    playbook_key = form.getfirst("playbook", "")
    inventory_key = form.getfirst("inventory_key", "")
    hosts = form.getlist("hosts")
    user  = (form.getfirst("user") or DEFAULT_USER).strip()

    if playbook_key not in PLAYBOOKS:
        header_ok(); print("<pre>Invalid playbook</pre>"); return
    if inventory_key not in INVENTORIES:
        header_ok(); print("<pre>Invalid inventory</pre>"); return
    if not hosts:
        header_ok(); print("<pre>No hosts selected</pre>"); return

    playbook_path  = PLAYBOOKS[playbook_key]["path"]
    inventory_path = INVENTORIES[inventory_key]["path"]

    effective_user = PLAYBOOKS[playbook_key].get("force_ssh_user", user)

    ensure_dirs()
    local_tmp = os.path.join(RUN_TMP, "ansible-local")
    Path(local_tmp).mkdir(parents=True, exist_ok=True)

    cmd = [ANSIBLE_BIN, "-i", inventory_path, playbook_path, "--limit", ",".join(hosts), "-u", effective_user]

    if USE_SUDO:
        cmd = [SUDO_BIN, "-n", "--"] + cmd

    env = os.environ.copy()
    env["LANG"] = "C.UTF-8"
    env["HOME"] = RUN_HOME
    env["TMPDIR"] = RUN_TMP
    env["ANSIBLE_LOCAL_TEMP"] = local_tmp
    env["ANSIBLE_REMOTE_TMP"] = "/tmp"
    env["ANSIBLE_HOST_KEY_CHECKING"] = "False"

    job_id = new_job_id()
    jp = job_paths(job_id)
    Path(jp["dir"]).mkdir(parents=True, exist_ok=True)

    logf = open(jp["log"], "w", buffering=1, encoding="utf-8", errors="replace")
    try:
        proc = subprocess.Popen(
            cmd, stdout=logf, stderr=subprocess.STDOUT, env=env, cwd=Path(playbook_path).parent
        )
    except Exception as e:
        logf.write("Failed: %s\n" % str(e))
        logf.close()
        header_ok(); print("<pre>%s</pre>" % safe(str(e))); return

    meta = {"playbook_key": playbook_key, "inventory_key": inventory_key, "hosts": hosts, "pid": proc.pid, "start_ts": int(time.time())}
    write_json(jp["meta"], meta)

    with open(os.devnull, "wb") as devnull:
        subprocess.Popen(["bash", "-lc", f"while kill -0 {proc.pid} 2>/dev/null; do sleep 1; done; echo $? > {quote(jp['rc'])}"], stdout=devnull, stderr=devnull)

    header_ok()
    print(f"<html><head><meta http-equiv='refresh' content='0; URL=?action=watch&job={job_id}'></head><body>Starting...</body></html>")


# ---------------- WATCH ----------------
def render_watch(form):
    job_id = form.getfirst("job", "")
    jp = job_paths(job_id)
    if not os.path.isdir(jp["dir"]):
        header_ok(); print("<pre>Unknown job</pre>"); return

    meta = read_json(jp["meta"], {})
    rc = None
    if os.path.exists(jp["rc"]):
        with open(jp["rc"], "r") as f:
            try: rc = int((f.read() or "1").strip())
            except: rc = 1

    header_ok()
    print("<html><head><title>Result</title></head><body><h1>Playbook Result</h1>")
    with open(jp["log"], "r", encoding="utf-8", errors="replace") as f:
        print("<h2>Playbook Output</h2><pre>%s</pre>" % safe(f.read()))

    # --- Embed latest HTML report if available ---
    if rc is not None:
        report = find_latest_report()
        if report:
            print(f"<h2>HTML Report</h2>")
            print(f"<iframe src='file://{report}' width='100%' height='600' style='border:1px solid #ccc;'></iframe>")
        else:
            print("<p><em>No HTML report found.</em></p>")

    print("</body></html>")


# ---------------- MAIN ----------------
def main():
    form = cgi.FieldStorage()
    action = form.getfirst("action", "")
    if action == "start":
        start_job(form)
    elif action == "watch":
        render_watch(form)
    else:
        header_ok(); print("<h1>Runner</h1><form method='post'><input type='hidden' name='action' value='start'/><button>Run</button></form>")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ansible Playbook CGI Runner — filtered inventories + regions/hosts + output + reports
- Hides file paths in UI (labels only)
- Inventory list filtered by selected playbook
- Regions (INI groups) + scrollable hosts + select all/none
- Intel: force SSH user cloudadmin
- AMD: SSH as serveradmin (what you type) and sudo to awsuser (--become-user awsuser)
- Runs ansible-playbook and shows output inline (masked command)
- Browse generated HTML reports securely
- Polished UI; Python 3.7 compatible
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
        "suggest_ssh_user": "serveradmin",
        "become_user": "awsuser",
    },
}

INVENTORIES = {
    "intel-inv": {"label": "Intel Inventory", "path": "/var/www/cgi-bin/intel-inv.ini"},
    "amd-inv":   {"label": "AMD Inventory",   "path": "/var/www/cgi-bin/amd-inv.ini"},
}

REPORT_BASES = [
    "/var/www/cgi-bin/reports",
]

ANSIBLE_BIN = shutil.which("ansible-playbook") or "/usr/bin/ansible-playbook"
DEFAULT_USER = os.environ.get("ANSIBLE_SSH_USER", "ansadmin")
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


# ---------- Reports ----------
def find_reports(hosts, since_ts, limit=200):
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
        <a class="btn" href="">Back</a>
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
        render_form("Invalid playbook selected.", form); return
    if inventory_key not in INVENTORIES or inventory_key not in PLAYBOOKS[playbook_key]["inventories"]:
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

    if forced_user:
        user = forced_user
    elif suggest_user and not form.getfirst("user"):
        user = suggest_user
    if not USER_RE.match(user):
        render_form("Invalid SSH user.", form); return

    playbook_path  = pb_meta["path"]
    inventory_path = INVENTORIES[inventory_key]["path"]

    Path(RUN_HOME).mkdir(parents=True, exist_ok=True)
    Path(RUN_TMP).mkdir(parents=True, exist_ok=True)
    local_tmp = os.path.join(RUN_TMP, "ansible-local")
    Path(local_tmp).mkdir(parents=True, exist_ok=True)

    cmd = [ANSIBLE_BIN, "-i", inventory_path, playbook_path, "--limit", ",".join(hosts), "-u", user]
    if do_check:
        cmd.append("--check")

    if become_user:
        cmd += ["-b", "--become-user", become_user]
    elif do_become:
        cmd.append("-b")

    if tags:
        cmd += ["--tags", tags]

    # Auth secrets: support ansible_password and ansible_ssh_pass
    if ssh_pass:
        cmd += ["-e", f"ansible_password={ssh_pass}"]
        cmd += ["-e", f"ansible_ssh_pass={ssh_pass}"]
    if become_pass:
        cmd += ["-e", f"ansible_become_password={become_pass}"]

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
        output = (e.output or "") + f"\nERROR: Execution timed out after {RUN_TIMEOUT_SECS}s.\n"
        rc = 124
    except Exception as e:
        header_ok(); print("<pre>{}</pre>".format(safe(str(e)))); return

    since_ts = max(start_ts - 5, time.time() - 2 * 3600)
    recent_reports = find_reports(hosts, since_ts)

    header_ok()
    status = "✅ SUCCESS" if rc == 0 else f"❌ FAILED (rc={rc})"
    masked_cmd = "ansible-playbook [redacted]"
    recent_html = render_reports_list(
        "Reports (last 2h, matching selected hosts)",
        recent_reports,
        "Roots: {}".format(", ".join(REPORT_BASES)),
    )

    html_out = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Run Result — Ansible Playbook CGI Runner</title>
  <style>
    body {{ font-family: system-ui, Segoe UI, Roboto, Arial, sans-serif; margin: 24px; }}
    .card {{ max-width: 1000px; margin: auto; padding: 20px; border: 1px solid #ddd; border-radius: 12px; box-shadow: 0 2px 6px rgba(0,0,0,.05); }}
    pre {{ background: #0b

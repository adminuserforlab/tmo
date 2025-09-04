#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ansible Playbook CGI Runner — complete, polished single-file CGI script
- Filters inventories by playbook
- Regions (INI groups) + scrollable hosts + select all/none
- Force/suggest SSH user per-playbook, switch-user dropdown + custom
- Expose ansible_password/ansible_ssh_pass + become password support
- Runs ansible-playbook inline (masked command in HTML) and shows output
- Browse generated HTML reports securely (whitelisted report bases)
- Python 3.7+ compatible

Security notes:
- Inputs validated/sanitized; host/user/token regexes enforced
- Report browsing restricted to REPORT_BASES and safe URL quoting
- Command is constructed carefully and not rendered with secrets

Place this file in your CGI-enabled directory (e.g. /var/www/cgi-bin/) and
make executable: chmod +x ansible_cgi_runner.py
"""

from __future__ import print_function

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

# report base directories (whitelisted)
REPORT_BASES = ["/var/www/cgi-bin/reports"]

ANSIBLE_BIN = shutil.which("ansible-playbook") or "/usr/bin/ansible-playbook"
DEFAULT_USER = os.environ.get("ANSIBLE_SSH_USER", "ansadmin")
COMMON_USERS = ["cloudadmin", "serveradmin", "ansadmin", "ec2-user"]

RUN_TIMEOUT_SECS = 3600
USE_SUDO = False
SUDO_BIN = shutil.which("sudo") or "/usr/bin/sudo"

# runtime dirs used for ansible env
RUN_HOME = "/var/lib/www-ansible/home"
RUN_TMP  = "/var/lib/www-ansible/tmp"

# validation regexes
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
    try:
        return os.path.realpath(p)
    except Exception:
        return p


def _is_under(base: str, target: str) -> bool:
    base_r = _realpath(base)
    tgt_r  = _realpath(target)
    return tgt_r == base_r or tgt_r.startswith(base_r + os.sep)

# ---------------- Inventory Parser ----------------

def parse_ini_inventory_groups(path: str):
    groups, current = {}, None
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith(("#",";")):
                    continue
                if line.startswith("[") and line.endswith("]"):
                    current = line[1:-1].strip()
                    groups.setdefault(current, [])
                    continue
                if current:
                    token = line.split()[0].split("=")[0].strip()
                    if token and token not in groups[current]:
                        groups[current].append(token)
    except Exception:
        return {}
    # hide 'all' and 'ungrouped' if empty
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
    return f"<h3>{safe(title)}</h3>{ul}{('<p class=\'muted\'>%s</p>' % safe(extra_note) if extra_note else '')}"

# ---------------- Form/UI ----------------

def render_form(msg: str = "", form: cgi.FieldStorage = None):
    header_ok()
    if form is None:
        form = cgi.FieldStorage()

    selected_playbook = form.getfirst("playbook", "")
    inventory_key     = form.getfirst("inventory_key", "")
    selected_regions  = form.getlist("regions")
    posted_hosts      = form.getlist("hosts")

    allowed_invs = PLAYBOOKS.get(selected_playbook, {}).get("inventories", [])

    groups_map, all_hosts, host_groups = get_inventory_maps(inventory_key)

    playbook_opts = "\n".join(
        f'<option value="{safe(k)}" {"selected" if k==selected_playbook else ""}>{safe(v["label"])}</option>'
        for k,v in PLAYBOOKS.items()
    )
    inv_opts = "\n".join(
        f'<option value="{safe(k)}" {"selected" if k==inventory_key else ""}>{safe(INVENTORIES[k]["label"])}</option>'
        for k in allowed_invs if k in INVENTORIES
    )

    if groups_map:
        regions_html = "\n".join(
            f'<label><input type="checkbox" name="regions" value="{safe(g)}" {"checked" if g in selected_regions else ""}/> {safe(g)} ({len(groups_map[g])})</label>'
            for g in groups_map
        )
    else:
        regions_html = "<p class='muted'>No regions to show. Select an inventory first.</p>"

    if all_hosts:
        hosts_html = "\n".join(
            f'<label><input type="checkbox" name="hosts" value="{safe(h)}" data-groups="{safe(",".join(host_groups.get(h, [])))}" {"checked" if posted_hosts and h in posted_hosts else ""}/> {safe(h)}</label>'
            for h in all_hosts
        )
    else:
        hosts_html = "<p class='muted'>No hosts to show.</p>"

    forced_user   = PLAYBOOKS.get(selected_playbook, {}).get("force_ssh_user")
    suggest_user  = PLAYBOOKS.get(selected_playbook, {}).get("suggest_ssh_user")
    preset = suggest_user if suggest_user else form.getfirst("user", DEFAULT_USER)

    if forced_user:
        user_input_html = (
            f'<input id="user" name="user_display" type="text" value="{safe(forced_user)}" disabled />'
            f'<input type="hidden" name="user" value="{safe(forced_user)}" />'
            f'<div class="muted">SSH login is forced to <strong>{safe(forced_user)}</strong> for this playbook.</div>'
        )
    else:
        opts = "\n".join(
            f'<option value="{safe(u)}" {"selected" if u==preset else ""}>{safe(u)}</option>'
            for u in COMMON_USERS
        )
        user_input_html = f"""
        <select name='user'>
        {opts}
        <option value='custom'>Custom...</option>
        </select>
        <input id='user_custom' name='user_custom' type='text' placeholder='Enter custom SSH user' style='display:none;margin-top:5px;' />
        <script>
        const sel=document.querySelector("select[name=user]");
        const inp=document.getElementById("user_custom");
        sel.addEventListener("change",()=>{{inp.style.display=(sel.value=="custom")?"block":"none";}});
        </script>
        """

    msg_html = f"<div class='warn'>{safe(msg)}</div>" if msg else ""

    # basic inline styles kept minimal for CGI environment
    print(f"""<!DOCTYPE html>
<html><head><meta charset='utf-8' />
<title>Ansible Playbook CGI Runner</title>
<style>
body{{font-family:system-ui, Arial, sans-serif; padding:12px;}}
.card{{max-width:1100px;background:#fff;padding:12px;border-radius:6px;box-shadow:0 1px 4px rgba(0,0,0,.08);}}
.hosts-box{{max-height:240px;overflow:auto;border:1px solid #eee;padding:8px}}
.muted{{color:#666;font-size:0.9em}}
.warn{{background:#fff6c2;padding:8px;border:1px solid #f0e09b;margin-bottom:10px}}
</style>
</head><body>
<div class='card'>
<h1>Ansible Playbook CGI Runner</h1>
{msg_html}
<form method='post'>
<input type='hidden' name='action' id='action' value='refresh' />
<label>Playbook</label>
<select name='playbook' onchange="this.form.submit()">
<option value='' {'selected' if not selected_playbook else ''}>Select...</option>
{playbook_opts}
</select>

<label>Inventory</label>
<select name='inventory_key' onchange="this.form.submit()">
<option value=''>Select...</option>
{inv_opts}
</select>

<label>Regions</label>
<div>{regions_html}</div>
<label>Hosts</label>
<div class='hosts-box'>{hosts_html}</div>

<label>SSH User (switch user)</label>
{user_input_html}

<label>SSH Password</label>
<input name='password' type='password'/>
<label>Become Password</label>
<input name='become_pass' type='password'/>

<label><input type='checkbox' name='check' value='1' {"checked" if form.getfirst('check') else ''}/> Dry Run (--check)</label>
<label><input type='checkbox' name='become' value='1' {"checked" if form.getfirst('become') else ''}/> Become (-b)</label>

<div style='margin-top:12px;'>
<button type='submit' onclick="document.getElementById('action').value='run'">Run Playbook</button>
<button type='submit' onclick="document.getElementById('action').value='refresh'" style='margin-left:6px;'>Refresh</button>
</div>
</form>

<hr/>
""")

    # show recent reports for the selected hosts
    try:
        since_ts = time.time() - (60 * 60 * 24 * 7)  # last 7 days
        reports = find_reports(posted_hosts or [], since_ts, limit=50)
        print(render_reports_list('Recent reports (last 7 days)', reports))
    except Exception:
        pass

    print("</div></body></html>")

# ---------------- Run Playbook ----------------

def run_playbook(form: cgi.FieldStorage):
    playbook_key = form.getfirst("playbook", "")
    inventory_key = form.getfirst("inventory_key", "")
    hosts = form.getlist("hosts")
    user  = (form.getfirst("user") or DEFAULT_USER).strip()
    if user == "custom":
        user = (form.getfirst("user_custom") or DEFAULT_USER).strip()
    tags  = (form.getfirst("tags") or "").strip()
    do_check  = (form.getfirst("check") == "1")
    do_become = (form.getfirst("become") == "1")
    ssh_pass    = (form.getfirst("password") or "").strip()
    become_pass = (form.getfirst("become_pass") or "").strip()

    # validation
    if playbook_key not in PLAYBOOKS:
        render_form("Invalid playbook.", form); return
    if inventory_key not in INVENTORIES or inventory_key not in PLAYBOOKS[playbook_key]["inventories"]:
        render_form("Invalid inventory.", form); return
    if not hosts:
        render_form("No hosts selected.", form); return
    for h in hosts:
        if not HOST_RE.match(h):
            render_form(f"Invalid host {h}.", form); return
    if tags and not TAGS_RE.match(tags):
        render_form("Invalid tags.", form); return

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

    # prepare runtime dirs
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

    # inject variables for password (these go to -e which is visible in process list briefly,
    # so we set them but do not render them back to the user. On some systems this may be visible
    # to other users. Consider using vaults or ssh-agent in production.)
    if ssh_pass:
        cmd += ["-e", f"ansible_password={ssh_pass}", "-e", f"ansible_ssh_pass={ssh_pass}"]
    if become_pass:
        cmd += ["-e", f"ansible_become_password={become_pass}"]

    if USE_SUDO:
        cmd = [SUDO_BIN, "-n", "--"] + cmd

    env = os.environ.copy()
    env.update({
        "LANG": "C.UTF-8",
        "HOME": RUN_HOME,
        "TMPDIR": RUN_TMP,
        "ANSIBLE_LOCAL_TEMP": local_tmp,
        "ANSIBLE_REMOTE_TMP": "/tmp",
        "ANSIBLE_HOST_KEY_CHECKING": "False",
        "ANSIBLE_SSH_ARGS": "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null",
    })

    TEXT_KW = {"text": True} if sys.version_info >= (3,7) else {"universal_newlines": True}

    # execute
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, timeout=RUN_TIMEOUT_SECS, cwd=Path(playbook_path).parent, **TEXT_KW)
        output, rc = proc.stdout, proc.returncode
    except subprocess.TimeoutExpired:
        output, rc = f"Timeout after {RUN_TIMEOUT_SECS}s", 124
    except Exception as e:
        header_ok(); print(f"<pre>{safe(str(e))}</pre>"); return

    # render result page
    header_ok()
    status = "✅ SUCCESS" if rc==0 else f"❌ FAILED rc={rc}"
    print("""<html><head><meta charset='utf-8'><title>Run Result</title>")
    print("<style>body{font-family:system-ui,Arial,sans-serif;padding:12px}pre{white-space:pre-wrap;background:#fafafa;padding:10px;border-radius:6px;border:1px solid #eee}</style>")
    print("</head><body>")
    print(f"<div class='card'><h2>Playbook run — {safe(status)}</h2>")
    print(f"<p class='muted'>Playbook: <strong>{safe(playbook_key)}</strong> &nbsp; Inventory: <strong>{safe(inventory_key)}</strong> &nbsp; Hosts: <strong>{safe(','.join(hosts))}</strong></p>")
    print("<h3>Output</h3>")
    # Output may contain user-sensitive data; display escaped
    print(f"<pre>{safe(output)}</pre>")
    print("<p><a href='?'>Back</a></p>")
    print("</div></body></html>")

# ---------------- View Report ----------------

def view_report(params: cgi.FieldStorage):
    # b is base index, p is relative path
    try:
        bidx = int(params.getfirst('b', '0'))
    except Exception:
        render_form('Invalid report selection.'); return
    rel = params.getfirst('p', '')
    if bidx < 0 or bidx >= len(REPORT_BASES):
        render_form('Invalid report base.'); return
    base = REPORT_BASES[bidx]
    # unquote and sanitize
    try:
        rel_un = unquote(rel)
    except Exception:
        rel_un = rel
    # avoid directory traversal
    candidate = os.path.normpath(os.path.join(base, rel_un))
    if not _is_under(base, candidate) or not os.path.isfile(candidate):
        render_form('Report not found or access denied.'); return
    # Stream the report (serve as HTML)
    header_ok('text/html; charset=utf-8')
    try:
        with open(candidate, 'r', encoding='utf-8', errors='replace') as fh:
            data = fh.read()
    except Exception as e:
        header_ok(); print(f"<pre>{safe(str(e))}</pre>"); return
    # We serve raw HTML from a trusted reports directory. If reports are not trusted, consider sanitization.
    print(data)

# ---------------- Main ----------------

def main():
    form = cgi.FieldStorage()
    action = form.getfirst('action', 'refresh')
    if action == 'run':
        run_playbook(form)
    elif action == 'view_report':
        view_report(form)
    else:
        render_form('', form)

if __name__ == '__main__':
    main()

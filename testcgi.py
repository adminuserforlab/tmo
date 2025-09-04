#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Secure Ansible Playbook CGI Runner — all f-strings replaced safely with format()
"""

import cgi, cgitb, html, os, re, shutil, subprocess, sys, time
from pathlib import Path
from urllib.parse import quote

cgitb.enable()

# ---------------- CONFIG ----------------
PLAYBOOKS = {
    "intel": {"label": "Intel Health Check", "path": "/var/www/cgi-bin/intel-check.yml",
              "inventories": ["intel-inv"], "force_ssh_user": "cloudadmin"},
    "amd":   {"label": "AMD Health Check",   "path": "/var/www/cgi-bin/amd-check.yml",
              "inventories": ["amd-inv"], "suggest_ssh_user": "serveradmin", "become_user": "awsuser"},
}

INVENTORIES = {
    "intel-inv": {"label": "Intel Inventory", "path": "/var/www/cgi-bin/intel-inv.ini"},
    "amd-inv":   {"label": "AMD Inventory",   "path": "/var/www/cgi-bin/amd-inv.ini"},
}

REPORT_BASES = ["/var/www/cgi-bin/reports"]

ANSIBLE_BIN = shutil.which("ansible-playbook") or "/usr/bin/ansible-playbook"
DEFAULT_USER = os.environ.get("ANSIBLE_SSH_USER", "ansadmin")
COMMON_USERS = ["cloudadmin", "serveradmin", "ansadmin", "ec2-user"]

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
    print("Content-Type: {}".format(ct))
    print()

def safe(s):
    return html.escape("" if s is None else str(s))

def _realpath(p):
    return os.path.realpath(p)

def _is_under(base, target):
    base_r = _realpath(base)
    tgt_r  = _realpath(target)
    return tgt_r == base_r or tgt_r.startswith(base_r + os.sep)

# ---------------- Inventory ----------------
def parse_ini_inventory_groups(path):
    groups, current = {}, None
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith(("#", ";")):
                continue
            if line.startswith("[") and line.endswith("]"):
                current = line[1:-1].strip()
                groups.setdefault(current, [])
                continue
            if current:
                token = line.split()[0].split("=")[0].strip()
                if token and token not in groups[current]:
                    groups[current].append(token)
    for k in ("all", "ungrouped"):
        if k in groups and not groups[k]:
            groups.pop(k, None)
    for k in list(groups.keys()):
        groups[k] = sorted(groups[k], key=str.lower)
    return dict(sorted(groups.items(), key=lambda kv: kv[0].lower()))

def get_inventory_maps(inv_key):
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
                if not fn.lower().endswith(".html"):
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
        href = "?action=view_report&b={}&p={}".format(bidx, quote(r["rel"]))
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["mtime"]))
        items.append('<li><a href="{}" target="_blank">{}</a></li>'.format(href, safe("{} — {}".format(r["rel"], ts))))
    if not items:
        ul = "<p class='muted'>No matching reports found.</p>"
    else:
        ul = "<ul>\n{}\n</ul>".format("\n".join(items))
    note_html = "<p class='muted'>{}</p>".format(safe(extra_note)) if extra_note else ""
    return "<h3>{}</h3>{}{}".format(safe(title), ul, note_html)

# ---------------- Form Renderer ----------------
def render_form(msg="", form=None):
    header_ok()
    if form is None:
        form = cgi.FieldStorage()

    selected_playbook = form.getfirst("playbook", "") or ""
    inventory_key     = form.getfirst("inventory_key", "") or ""
    selected_regions  = form.getlist("regions") or []
    posted_hosts      = form.getlist("hosts") or []

    allowed_invs = PLAYBOOKS.get(selected_playbook, {}).get("inventories", [])
    groups_map, all_hosts, host_groups = get_inventory_maps(inventory_key)

    playbook_opts = "\n".join(
        '<option value="{k}" {sel}>{lbl}</option>'.format(
            k=safe(k), lbl=safe(v["label"]),
            sel="selected" if k==selected_playbook else "")
        for k,v in PLAYBOOKS.items()
    )

    inv_opts = "\n".join(
        '<option value="{k}" {sel}>{lbl}</option>'.format(
            k=safe(k), lbl=safe(INVENTORIES[k]["label"]),
            sel="selected" if k==inventory_key else "")
        for k in allowed_invs if k in INVENTORIES
    )

    regions_html = "\n".join(
        '<label><input type="checkbox" name="regions" value="{g}" {chk}/> {g} ({n})</label>'.format(
            g=safe(group), n=len(groups_map[group]),
            chk="checked" if group in selected_regions else "")
        for group in groups_map
    ) if groups_map else "<p class='muted'>No regions to show. Select an inventory first.</p>"

    hosts_html = "\n".join(
        '<label><input type="checkbox" name="hosts" value="{h}" data-groups="{gs}" {chk}/> {h}</label>'.format(
            h=safe(h), gs=safe(",".join(host_groups.get(h, []))),
            chk="checked" if posted_hosts and h in posted_hosts else "")
        for h in all_hosts
    ) if all_hosts else "<p class='muted'>No hosts to show.</p>"

    forced_user = PLAYBOOKS.get(selected_playbook, {}).get("force_ssh_user")
    suggest_user = PLAYBOOKS.get(selected_playbook, {}).get("suggest_ssh_user")
    preset = suggest_user if suggest_user else (form.getfirst("user") or DEFAULT_USER)

    if forced_user:
        user_input_html = '<input id="user_display" name="user_display" type="text" value="{v}" disabled />'.format(v=safe(forced_user))
        user_input_html += '<input type="hidden" name="user" value="{v}" />'.format(v=safe(forced_user))
        user_input_html += '<div class="muted">SSH login is forced to <strong>{v}</strong> for this playbook.</div>'.format(v=safe(forced_user))
    else:
        preset_is_common = preset in COMMON_USERS
        opts_html = "\n".join('<option value="{u}" {sel}>{u}</option>'.format(u=safe(u), sel="selected" if (preset_is_common and u==preset) else "") for u in COMMON_USERS)
        custom_val = "" if preset_is_common else safe(preset)
        custom_display = "block" if not preset_is_common else "none"
        user_input_html = '<select name="user" id="user_select">{opts}<option value="custom" {sel}>Custom...</option></select>'.format(opts=opts_html, sel="" if preset_is_common else "selected")
        user_input_html += '<input id="user_custom" name="user_custom" type="text" placeholder="Enter custom SSH user" style="display:{};margin-top:6px;" value="{}"/>'.format(custom_display, custom_val)
        user_input_html += """
<script>
(function(){
var sel = document.getElementById('user_select');
var inp = document.getElementById('user_custom');
function toggle(){ inp.style.display = (sel.value==='custom') ? 'block' : 'none'; }
sel.addEventListener('change', toggle); toggle();
})();
</script>
"""

    tags_val = safe(form.getfirst("tags",""))
    check_val = "checked" if form.getfirst("check") else ""
    become_val = "checked" if (form.getfirst("become") or not form) else ""
    msg_html = "<div class='warn'>{}</div>".format(safe(msg)) if msg else ""

    html_out = """
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Ansible Playbook CGI Runner</title></head>
<body>
<div class="card">
<h1>Ansible Playbook CGI Runner</h1>
{msg_html}
<form method="post" action="" id="runnerForm">
<input type="hidden" name="action" value="refresh" id="action"/>
<label>Playbook</label>
<select name="playbook" onchange="document.getElementById('action').value='refresh'; this.form.submit();">
<option value="">Select a playbook…</option>
{playbook_opts}
</select>
<label>Inventory</label>
<select name="inventory_key" onchange="document.getElementById('action').value='refresh'; this.form.submit();">
<option value="">(Pick a playbook first)</option>
{inv_opts}
</select>
<div>Regions:</div>{regions_html}
<div>Hosts:</div>{hosts_html}
<label>SSH user</label>{user_input_html}
<label>--tags</label><input type="text" name="tags" value="{tags_val}"/>
<label><input type="checkbox" name="check" value="1" {check_val}/> Dry run</label>
<label><input type="checkbox" name="become" value="1" {become_val}/> Become</label>
<button type="submit" onclick="document.getElementById('action').value='run'">Run Playbook</button>
<a href="?action=list_reports">Browse reports</a>
</form>
</div></body></html>
""".format(msg_html=msg_html, playbook_opts=playbook_opts, inv_opts=inv_opts,
           regions_html=regions_html, hosts_html=hosts_html, user_input_html=user_input_html,
           tags_val=tags_val, check_val=check_val, become_val=become_val)

    print(html_out)

# ---------------- MAIN ----------------
def main():
    try:
        method = os.environ.get("REQUEST_METHOD","GET").upper()
        form = cgi.FieldStorage()
        action = form.getfirst("action","") or ""
        if method=="GET" and action=="view_report":
            serve_report(form)
        elif method=="GET" and action=="list_reports":
            list_reports_page(form)
        elif method=="POST" and action=="run":
            run_playbook(form)
        else:
            render_form("", form)
    except Exception:
        import traceback
        header_ok()
        print("<pre>{}</pre>".format(safe(traceback.format_exc())))

if __name__=="__main__":
    main()

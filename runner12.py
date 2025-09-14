#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Minimal Ansible Playbook CGI Runner (brace-safe rendering)
- Uses old-style % string formatting so CSS/JS { } don’t collide with .format()
- Simple form with playbook/inventory/host list (demo data)
- Python 3.7 compatible
"""

import cgi
import cgitb
import html
import os
from pathlib import Path

cgitb.enable()

# --- Demo config (replace with your own) ---
PLAYBOOKS = {
    "intel": "Intel Health Check",
    "amd":   "AMD Health Check",
}
INVENTORIES = {
    "intel-inv": "Intel Inventory",
    "amd-inv":   "AMD Inventory",
}

def header_ok(ct="text/html; charset=utf-8"):
    print("Content-Type: " + ct)
    print()

def safe(x):  # small helper
    return html.escape("" if x is None else str(x))

def render_form(msg="", form=None):
    header_ok()
    if form is None:
        form = cgi.FieldStorage()

    selected_playbook = form.getfirst("playbook", "")
    inventory_key     = form.getfirst("inventory_key", "")
    user_val          = form.getfirst("user", "ansadmin") or "ansadmin"

    # dropdown options (labels only)
    pb_opts = "\n".join(
        '<option value="%s" %s>%s</option>' %
        (safe(k), ("selected" if k == selected_playbook else ""), safe(v))
        for k, v in PLAYBOOKS.items()
    )
    inv_opts = "\n".join(
        '<option value="%s" %s>%s</option>' %
        (safe(k), ("selected" if k == inventory_key else ""), safe(v))
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
    select, input[type=text] { width: 100%; padding: 10px; border: 1px solid #ccc; border-radius: 8px; }
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
  </style>
</head>
<body>
  <div class="card">
    <h1>Ansible Playbook CGI Runner</h1>
    %(msg_html)s
    <form id="runnerForm" method="post" action="">
      <input type="hidden" name="action" id="action" value="refresh" />

      <label for="playbook">Playbook</label>
      <select id="playbook" name="playbook">
        <option value="" %(sel_pb)s>Select a playbook…</option>
        %(pb_opts)s
      </select>

      <label for="inventory_key">Inventory</label>
      <select id="inventory_key" name="inventory_key">
        <option value="">(Pick an inventory)</option>
        %(inv_opts)s
      </select>
      <div class="muted">Pick an inventory, then choose options below.</div>

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

      <label><input type="checkbox" name="check" value="1" %(check_val)s/> Dry run (--check)</label>
      <label><input type="checkbox" name="become" value="1" %(become_val)s/> Become (-b)</label>

      <div class="actions">
        <button class="btn" type="submit" onclick="document.getElementById('action').value='run'">Run Playbook</button>
        <a class="btn" href="#" onclick="document.getElementById('action').value='refresh'; document.getElementById('runnerForm').submit(); return false;">Refresh</a>
      </div>
    </form>
  </div>
</body>
</html>
"""
    print(html_tpl % {
        "msg_html": msg_html,
        "sel_pb": ("selected" if not selected_playbook else ""),
        "pb_opts": pb_opts,
        "inv_opts": inv_opts,
        "user_val": safe(user_val),
        "tags_val": safe(form.getfirst("tags","")),
        "check_val": ("checked" if form.getfirst("check") else ""),
        "become_val": ("checked" if form.getfirst("become") else ""),
    })

def main():
    try:
        form = cgi.FieldStorage()
        # This demo only renders the form (no subprocess run) to showcase brace-safe HTML.
        render_form(form=form)
    except Exception as e:
        header_ok()
        import traceback
        print("<pre>%s</pre>" % html.escape(traceback.format_exc()))

if __name__ == "__main__":
    main()

#!/usr/bin/env python3

import cgi
import cgitb
import html
import os
import re
import shutil
import subprocess
import tempfile
import traceback
import configparser
from pathlib import Path

cgitb.enable()

PLAYBOOKS = {
    "test-pb": "/var/pb/test-playbook.yml",
    "upgrade": "/opt/ansible/playbooks/upgrade.yml",
    "rollback": "/opt/ansible/playbooks/rollback.yml",
}
INVENTORIES = {
    "test-inv": "/var/pb/test-inv.yml",
    "staging": "/opt/ansible/inv/staging.ini",
    "dev": "/opt/ansible/inv/dev.ini",
}

ANSIBLE_BIN = shutil.which("ansible-playbook") or "/usr/bin/ansible-playbook"
DEFAULT_USER = os.environ.get("ANSIBLE_SSH_USER", "ansadmin")
RUN_TIMEOUT_SECS = 3600
USE_SUDO = False
SUDO_BIN = shutil.which("sudo") or "/usr/bin/sudo"
RUN_HOME = "/var/lib/www-ansible/home"
RUN_TMP = "/var/lib/www-ansible/tmp"

HOST_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_.,-]+$")
TAGS_RE = re.compile(r"^[A-Za-z0-9_,.-]+$")
USER_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

def header_ok():
    print("Content-Type: text/html; charset=utf-8")
    print()

def parse_inventory_hosts(inv_path: str):
    parser = configparser.ConfigParser(allow_no_value=True, delimiters=(' ',))
    parser.optionxform = str
    with open(inv_path, "r") as f:
        parser.read_file(f)
    hosts = []
    for section in parser.sections():
        if section.startswith("group:") or section == "defaults":
            continue
        for host in parser.options(section):
            if HOST_RE.match(host):
                hosts.append(host)
    return sorted(set(hosts))

def render_form(msg: str = "", inventory_key: str = ""):
    header_ok()
    playbook_opts = "\n".join(
        f'<option value="{html.escape(k)}">{html.escape(k)} — {html.escape(v)}</option>'
        for k, v in PLAYBOOKS.items()
    )
    inv_opts = "\n".join(
        f'<option value="{html.escape(k)}" {"selected" if k == inventory_key else ""}>{html.escape(k)} — {html.escape(v)}</option>'
        for k, v in INVENTORIES.items()
    )

    print(f"""<!DOCTYPE html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <title>Ansible Playbook CGI Runner</title>
  <style>
    body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 24px; }}
    .card {{ max-width: 900px; margin: auto; padding: 20px; border: 1px solid #ddd; border-radius: 12px; box-shadow: 0 2px 6px rgba(0,0,0,.05); }}
    h1 {{ margin-top: 0; }}
    label {{ display:block; margin: 12px 0 6px; font-weight: 600; }}
    select, input[type=text] {{ width: 100%; padding: 10px; border: 1px solid #ccc; border-radius: 8px; }}
    .row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
    .muted {{ color: #666; font-size: 0.95em; }}
    .btn {{ background: #0d6efd; color: #fff; padding: 10px 16px; border: 0; border-radius: 8px; cursor: pointer; }}
    .warn {{ background: #fff3cd; border: 1px solid #ffeeba; padding: 8px 12px; border-radius: 8px; }}
    pre {{ background: #0b1020; color: #d1e7ff; padding: 12px; border-radius: 8px; overflow-x: auto; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>Ansible Playbook CGI Runner</h1>
    {('<div class="warn">' + html.escape(msg) + '</div>') if msg else ''}
    <form method="post" action="">
      <label for="playbook">Playbook (whitelisted)</label>
      <select id="playbook" name="playbook" required>
        <option value="" disabled selected>Select a playbook…</option>
        {playbook_opts}
      </select>

      <div class="row">
        <div>
          <label for="inventory_key">Inventory (whitelisted)</label>
          <select id="inventory_key" name="inventory_key" onchange="this.form.submit()">
            <option value="">(None – I'll enter hostnames)</option>
            {inv_opts}
          </select>
          <div class="muted">Use a static inventory, or leave blank to supply hostnames below.</div>
        </div>
        <div>
          <label for="limit">Limit (-l)</label>
          <input id="limit" name="limit" type="text" placeholder="groupname or host1,host2 (optional)" />
        </div>
      </div>

      <label for="hosts">Hostnames (when not using inventory)</label>
      <input id="hosts" name="hosts" type="text" placeholder="host1,host2.example.com (optional)" />
      <div class="muted">If provided, a temporary inventory with group [targets] will be created.</div>

""")
    if inventory_key in INVENTORIES:
        try:
            host_list = parse_inventory_hosts(INVENTORIES[inventory_key])
            if host_list:
                host_checkboxes = "\n".join(
                    f'<label><input type="checkbox" name="select_host" value="{html.escape(h)}" /> {html.escape(h)}</label>'
                    for h in host_list
                )
                print(f"""
                <label>Available Hosts (from inventory)</label>
                <div style="padding:12px;border:1px solid #ccc;border-radius:8px;">
                  {host_checkboxes}
                </div>
                <div class="muted">You can limit to one or more hosts here. Overrides 'Limit' if set.</div>
                """)
        except Exception as e:
            print(f'<div class="warn">Error parsing inventory: {html.escape(str(e))}</div>')

    print(f"""
      <div class="row">
        <div>
          <label for="user">SSH user (-u)</label>
          <input id="user" name="user" type="text" value="{html.escape(DEFAULT_USER)}" />
        </div>
        <div>
          <label for="tags">--tags (optional, comma-separated)</label>
          <input id="tags" name="tags" type="text" placeholder="setup,deploy" />
        </div>
      </div>

      <label><input type="checkbox" name="check" value="1" /> Dry run (--check)</label>
      <label><input type="checkbox" name="become" value="1" checked /> Become (-b)</label>

      <div style="margin-top:16px;">
        <button class="btn" type="submit">Run Playbook</button>
      </div>
    </form>
    <p style="margin-top:12px;"><a href="?diag=1">Diagnostics</a></p>
  </div>
</body>
</html>
""")

def do_run(form: cgi.FieldStorage):
    if not ANSIBLE_BIN or not os.path.exists(ANSIBLE_BIN):
        render_form(f"ansible-playbook not found at: {ANSIBLE_BIN}")
        return

    playbook_key = (form.getfirst("playbook") or "").strip()
    if playbook_key not in PLAYBOOKS:
        render_form("Invalid or missing playbook selected.")
        return
    playbook_path = PLAYBOOKS[playbook_key]

    inventory_key = (form.getfirst("inventory_key") or "").strip()
    tmp_inv_path = None
    inventory_path = None

    if inventory_key:
        if inventory_key not in INVENTORIES:
            render_form("Invalid inventory selection.")
            return
        inventory_path = INVENTORIES[inventory_key]
    else:
        hosts_csv = (form.getfirst("hosts") or "").strip()
        if hosts_csv:
            try:
                hosts = validate_hosts_csv(hosts_csv)
            except ValueError as e:
                render_form(str(e))
                return
            tf = tempfile.NamedTemporaryFile("w", delete=False, prefix="inv_", suffix=".ini")
            tf.write("[targets]\n")
            for h in hosts:
                tf.write(f"{h}\n")
            tf.flush()
            tf.close()
            tmp_inv_path = tf.name
            inventory_path = tmp_inv_path

    cmd = [ANSIBLE_BIN]
    if inventory_path:
        cmd += ["-i", inventory_path]

    selected_hosts = form.getlist("select_host")
    if selected_hosts:
        for h in selected_hosts:
            if not HOST_RE.match(h):
                render_form("Invalid host selected.")
                return
        cmd += ["-l", ",".join(selected_hosts)]
    else:
        limit = (form.getfirst("limit") or "").strip()
        if limit:
            if not TOKEN_RE.match(limit):
                render_form("Invalid characters in limit parameter.")
                return
            cmd += ["-l", limit]

    user = (form.getfirst("user") or DEFAULT_USER).strip()
    if not USER_RE.match(user):
        render_form("Invalid SSH user.")
        return
    cmd += ["-u", user]

    if form.getfirst("become") == "1":
        cmd += ["-b"]
    if form.getfirst("check") == "1":
        cmd += ["--check"]
    tags = (form.getfirst("tags") or "").strip()
    if tags:
        if not TAGS_RE.match(tags):
            render_form("Invalid characters in tags.")
            return
        cmd += ["--tags", tags]

    cmd.append(playbook_path)

    if USE_SUDO:
        cmd = [SUDO_BIN, "-n", "--"] + cmd

    env = os.environ.copy()
    env.setdefault("LANG", "C.UTF-8")
    env.setdefault("HOME", RUN_HOME)
    env.setdefault("TMPDIR", RUN_TMP)
    env.setdefault("ANSIBLE_HOST_KEY_CHECKING", "False")

    Path(RUN_HOME).mkdir(parents=True, exist_ok=True)
    Path(RUN_TMP).mkdir(parents=True, exist_ok=True)

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            text=True,
            timeout=RUN_TIMEOUT_SECS,
            cwd=Path(playbook_path).parent
        )
        output = proc.stdout
        rc = proc.returncode
    except subprocess.TimeoutExpired as e:
        output = (e.output or "") + f"\nERROR: Execution timed out after {RUN_TIMEOUT_SECS}s.\n"
        rc = 124
    except Exception:
        header_ok()
        print(f"<pre>{html.escape(traceback.format_exc())}</pre>")
        if tmp_inv_path and Path(tmp_inv_path).exists():
            try:
                os.unlink(tmp_inv_path)
            except Exception:
                pass
        return
    finally:
        if tmp_inv_path and Path(tmp_inv_path).exists():
            try:
                os.unlink(tmp_inv_path)
            except Exception:
                pass

    header_ok()
    status = "✅ SUCCESS" if rc == 0 else f"❌ FAILED (rc={rc})"
    safe_cmd = " ".join(html.escape(x) for x in cmd)
    print(f"""<!DOCTYPE html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <title>Run Result — Ansible Playbook CGI Runner</title>
  <style>
    body {{ font-family: system-ui, Segoe UI, Roboto, Arial, sans-serif; margin: 24px; }}
    .card {{ max-width: 1000px; margin: auto; padding: 20px; border: 1px solid #ddd; border-radius: 12px; box-shadow: 0 2px 6px rgba(0,0,0,.05); }}
    pre {{ background: #0b1020; color: #d1e7ff; padding: 12px; border-radius: 8px; overflow-x: auto; white-space: pre-wrap; }}
    .btn {{ background: #0d6efd; color: #fff; padding: 8px 14px; border: 0; border-radius: 8px; text-decoration: none; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>{status}</h1>
    <p><strong>Command:</strong> <code>{safe_cmd}</code></p>
    <h3>Output</h3>
    <pre>{html.escape(output)}</pre>
    <p><a class="btn" href="">Run another</a></p>
  </div>
</body>
</html>
""")

def diagnostics():
    header_ok()
    pb_list = "\n".join(f"{k}: {v}" for k, v in PLAYBOOKS.items())
    inv_list = "\n".join(f"{k}: {v}" for k, v in INVENTORIES.items())
    exists = Path(ANSIBLE_BIN).exists()
    print(f"""<!DOCTYPE html>
<html><head><meta charset=\"utf-8\"><title>Diagnostics</title></head>
<body style=\"font-family:system-ui,Segoe UI,Roboto,Arial,sans-serif;margin:24px;\">
<h1>Diagnostics</h1>
<ul>
  <li>ANSIBLE_BIN exists: <strong>{'yes' if exists else 'no'}</strong> ({html.escape(ANSIBLE_BIN)})</li>
  <li>RUN_HOME: {html.escape(RUN_HOME)}</li>
  <li>RUN_TMP: {html.escape(RUN_TMP)}</li>
</ul>
<h3>Playbooks</h3>
<pre>{html.escape(pb_list)}</pre>
<h3>Inventories</h3>
<pre>{html.escape(inv_list)}</pre>
<p><a href=\"./ansible_runner.py\">Back</a></p>
</body></html>""")

def validate_hosts_csv(hosts_csv: str):
    hosts = []
    for h in filter(None, [x.strip() for x in hosts_csv.split(",")]):
        if not HOST_RE.match(h):
            raise ValueError(f"Invalid hostname: {h}")
        hosts.append(h)
    if not hosts:
        raise ValueError("No valid hostnames provided")
    return hosts

def main():
    try:
        method = os.environ.get("REQUEST_METHOD", "GET").upper()
        if method == "GET" and os.environ.get("QUERY_STRING", "") == "diag=1":
            diagnostics()
        elif method == "POST":
            form = cgi.FieldStorage()
            do_run(form)
        else:
            qs = cgi.FieldStorage()
            render_form(inventory_key=qs.getfirst("inventory_key", ""))
    except Exception:
        header_ok()
        print(f"<pre>{html.escape(traceback.format_exc())}</pre>")

if __name__ == "__main__":
    main()

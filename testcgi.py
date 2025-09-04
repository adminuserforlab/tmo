#!/usr/bin/env python3
import cgi
import cgitb
import os
import subprocess
import sys

cgitb.enable()

RUN_HOME = "/tmp"
RUN_TMP = "/tmp"

def render_form():
    print("Content-Type: text/html\n")
    print("<html><head><title>Ansible CGI</title></head><body>")
    print("<h2>Run Ansible Playbook</h2>")
    print('<form method="post">')
    print('<label for="inv">Inventory file:</label>')
    print('<input type="text" id="inv" name="inv"><br><br>')
    print('<label for="play">Playbook:</label>')
    print('<input type="text" id="play" name="play"><br><br>')
    print('<label for="user">SSH User:</label>')
    print('<input type="text" id="user" name="user"><br><br>')
    print('<label for="pass">SSH Password (optional):</label>')
    print('<input type="password" id="pass" name="pass"><br><br>')
    print('<label for="become_user">Become User (optional):</label>')
    print('<input type="text" id="become_user" name="become_user"><br><br>')
    print('<input type="submit" value="Run">')
    print("</form></body></html>")

def run_playbook(form):
    play = form.getfirst("play")
    inv = form.getfirst("inv")
    ssh_user = form.getfirst("user")
    ssh_pass = form.getfirst("pass")
    become_user = form.getfirst("become_user")

    local_tmp = os.path.join(RUN_TMP, "ansible-tmp-" + str(os.getpid()))

    # ----- Build command -----
    cmd = ["ansible-playbook", "-i", inv, play]

    # Always use -u (like CLI)
    if ssh_user:
        cmd += ["-u", ssh_user]

    # Become support
    if become_user:
        cmd += ["-b", "--become-user", become_user]

    # Extra vars for password
    extra_vars = []
    if ssh_pass:
        extra_vars.append(f"ansible_ssh_pass={ssh_pass}")

    if extra_vars:
        cmd += ["-e", " ".join(extra_vars)]

    # ----- Environment -----
    env = os.environ.copy()
    env["LANG"] = "C.UTF-8"
    env["HOME"] = RUN_HOME
    env["TMPDIR"] = RUN_TMP
    env["ANSIBLE_LOCAL_TEMP"] = local_tmp
    env["ANSIBLE_REMOTE_TMP"] = "/tmp"
    env["ANSIBLE_HOST_KEY_CHECKING"] = "False"

    # Force password auth if password given
    env["ANSIBLE_SSH_ARGS"] = "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
    if ssh_pass:
        env["ANSIBLE_SSH_ARGS"] += " -o PubkeyAuthentication=no -o PreferredAuthentications=password"

    # Run playbook
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, text=True)
    output, _ = proc.communicate()

    return output

def main():
    form = cgi.FieldStorage()
    if "play" not in form or "inv" not in form or "user" not in form:
        render_form()
    else:
        output = run_playbook(form)
        print("Content-Type: text/plain\n")
        sys.stdout.write(output)

if __name__ == "__main__":
    main()

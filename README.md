# SSH Remote Control

Persistent SSH remote control plugin for Codex. It adds MCP tools for saved SSH profiles, long-lived SSH sessions, remote command execution, remote file reads and writes, upload and download, and recursive sync.

## What it solves

- Keep using local Codex App while operating on a VPS
- Reuse `~/.ssh/config` when you already have host aliases
- Persist remote sessions inside the plugin process instead of reconnecting on every command
- Move files between local and remote machines without leaving Codex

## What it does not solve

This plugin does not patch Codex Desktop into a native "remote workspace attach" app. The public `openai/codex` repository exposes plugin, MCP, app-server, and exec-server layers, but not the Desktop app UI source itself. This plugin targets the highest practical capability available from the public extension surface.

## Local install

1. Create the plugin marketplace entry if it does not already exist.
2. Install Python dependencies:

```bash
PLUGIN_DIR=~/plugins/ssh-remote-control
python3 -m venv "$PLUGIN_DIR/.venv"
"$PLUGIN_DIR/.venv/bin/python" -m pip install -e "$PLUGIN_DIR"
```

On Windows, the bootstrap script will look for `.venv\\Scripts\\python.exe` automatically.

3. Restart Codex App or reload plugins.
4. Install or enable `SSH Remote Control` from the local marketplace.

## First use

Example profile using SSH config:

```text
ssh_profile_save(
  name="prod",
  ssh_config_host="prod",
  remote_root="/srv/app",
  auth_mode="ssh_config"
)
```

Example direct host profile:

```text
ssh_profile_save(
  name="vps",
  host="203.0.113.10",
  username="root",
  remote_root="/root/project",
  auth_mode="password",
  password_storage="session_only"
)
```

Then connect and work:

```text
ssh_connect(profile_name="prod")
ssh_exec(profile_name="prod", command="git status")
ssh_read_file(profile_name="prod", remote_path="README.md")
ssh_write_file(profile_name="prod", remote_path=".env.example", content="...")
ssh_upload(profile_name="prod", local_path="./dist", remote_path="releases/dist")
ssh_sync(profile_name="prod", local_path="./src", remote_path="app/src", direction="upload")
ssh_disconnect(profile_name="prod")
```

## Repository publish flow

```bash
cd ~/plugins/ssh-remote-control
git init
git add .
git commit -m "feat: add ssh remote control Codex plugin"
gh repo create ssh-remote-control --private --source=. --push
```

If you publish under a different GitHub owner or repository name, update `.codex-plugin/plugin.json`.

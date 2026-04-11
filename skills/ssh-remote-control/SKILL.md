---
name: ssh-remote-control
description: Use when Codex needs to operate on a VPS or remote machine over SSH while staying in the local Codex App session.
---

# SSH Remote Control

Use this plugin when the user wants Codex to work against a remote host over SSH without moving the entire Codex runtime onto that server.

## Workflow

1. Save or update a profile with `ssh_profile_save`
2. Configure `allowed_exec_prefixes`, `allowed_read_roots`, `allowed_write_roots`, or plan to pass `confirm=true` for one-off high-impact operations
3. Connect with `ssh_connect`
4. Use `ssh_exec`, `ssh_read_file`, `ssh_write_file`, `ssh_upload`, `ssh_download`, or `ssh_sync`
5. Disconnect with `ssh_disconnect` when the session is no longer needed

## Notes

- Profiles may reuse `~/.ssh/config` via `ssh_config_host`
- `password_storage` supports `never`, `session_only`, and `keyring`
- `remote_root` is the default working directory and base path for relative file operations
- `ssh_connect`, `ssh_exec`, remote reads, remote writes, upload, download, and sync require `confirm=true` unless the action matches a profile allowlist
- This plugin does not turn Codex Desktop into a native remote workspace attach UI. It provides persistent SSH tools inside the local Codex session

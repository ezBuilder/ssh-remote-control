# SSH Remote Control

Persistent SSH remote control plugin for Codex. It adds MCP tools for saved SSH profiles, long-lived SSH sessions, remote command execution, remote file reads and writes, upload and download, and recursive sync.

It also ships a `codex-remote` CLI that starts a remote `codex app-server` over SSH, opens a local loopback tunnel, and launches local `codex --remote` against that remote workspace.

## What it solves

- Keep using local Codex App while operating on a VPS
- Reuse `~/.ssh/config` when you already have host aliases
- Persist remote sessions inside the plugin process instead of reconnecting on every command
- Move files between local and remote machines without leaving Codex
- Run local Codex CLI against a remote Codex app-server without exposing a public websocket

## What it does not solve

This repository does not patch Codex Desktop into a native always-on remote workspace UI. The public `openai/codex` repository exposes plugin, MCP, app-server, and exec-server layers, but not the Desktop app UI source itself. The supported production path today is the `codex-remote` CLI workflow.

## Local install

Fast path on macOS or Linux:

```bash
cd /Users/ezbuilder/workspace/ssh-remote-control
./scripts/install_local.sh
```

Custom launcher directory:

```bash
cd /Users/ezbuilder/workspace/ssh-remote-control
./scripts/install_local.sh --bin-dir "$HOME/.local/bin"
```

Manual path:

1. Create the plugin marketplace entry if it does not already exist.
2. Install Python dependencies:

```bash
PLUGIN_DIR=~/workspace/ssh-remote-control
python3 -m venv "$PLUGIN_DIR/.venv"
"$PLUGIN_DIR/.venv/bin/python" -m pip install -e "$PLUGIN_DIR"
```

On Windows, the bootstrap script will look for `.venv\\Scripts\\python.exe` automatically.

3. Restart Codex App or reload plugins.
4. Install or enable `SSH Remote Control` from the local marketplace.

If you want a global launcher on your machine, run:

```bash
cd /Users/ezbuilder/workspace/ssh-remote-control
.venv/bin/codex-remote install-cli
.venv/bin/codex-remote install-cli --shell-completion zsh
.venv/bin/codex-remote completion fish
```

`install-cli --shell-completion <bash|zsh|fish>` also writes a completion file into the standard per-shell user location.
`completion <bash|zsh|fish>` prints the shell script so you can inspect or install it manually.
`install_local.sh` creates `.venv` when needed, installs the package editable into that environment, and installs the `codex-remote` launcher unless you pass `--skip-install-cli`. Add `--shell-completion zsh` if you want the bootstrap script to install completions at the same time.

## CI and release

GitHub Actions workflows are included:

- [ci.yml](/Users/ezbuilder/workspace/ssh-remote-control/.github/workflows/ci.yml): runs the unified release packaging path in `--strict-release` mode on macOS and Ubuntu across Python 3.11 and 3.12.
- [release.yml](/Users/ezbuilder/workspace/ssh-remote-control/.github/workflows/release.yml): on `v*` tags, runs the same strict packaging path, verifies the tag version matches the package version, emits a tag-named plugin bundle zip, and attaches everything under `dist/` to a GitHub release.

Maintainer fast path:

```bash
cd /Users/ezbuilder/workspace/ssh-remote-control
codex-remote package --json
```

This runs the unit suite, builds wheel/sdist, builds the plugin zip bundle, and prints artifact paths. You can skip individual stages when iterating:

```bash
codex-remote package --json --skip-tests
codex-remote package --json --skip-build
codex-remote package --json --skip-bundle
codex-remote package --json --bundle-output dist/ssh-remote-control-plugin-v0.1.0.zip
codex-remote package --json --no-clean
codex-remote package --json --strict-release
```

By default `package` removes previously managed release outputs in `dist/` before rebuilding, so stale wheel/zip files do not leak into `SHA256SUMS` or `release-manifest.json`. Use `--no-clean` only when you intentionally want to preserve an existing `dist/` state.
`--strict-release` fails fast when the repo is dirty, when `.codex-plugin/plugin.json` and `pyproject.toml` disagree on the version, or when a CI tag version does not match the package version.
`package` also fails when no releasable artifacts were produced, even without `--strict-release`, so an empty or misconfigured `dist/` cannot be mistaken for a successful release build.

Successful `package` runs also write:

- [SHA256SUMS](/Users/ezbuilder/workspace/ssh-remote-control/dist/SHA256SUMS): sha256 checksums for every generated artifact
- [release-manifest.json](/Users/ezbuilder/workspace/ssh-remote-control/dist/release-manifest.json): machine-readable artifact metadata with file size, sha256, package version, git state, generation timestamp, and build environment

Because the release workflow publishes `dist/*`, these metadata files are attached to GitHub releases automatically.

You can build the plugin release bundle locally with:

```bash
cd /Users/ezbuilder/workspace/ssh-remote-control
python3 scripts/build_plugin_bundle.py --json
```

## Remote Codex CLI workflow

Requirements on the VPS:

- `codex` must be installed and available in `PATH`, or you can install it remotely with `codex-remote install-codex`
- SSH access must already work non-interactively, ideally through `~/.ssh/config`

Save a remote profile:

```bash
codex-remote init prod \
  --ssh-config-host llm.ezbuilder.app \
  --remote-root /srv/app \
  --codex-version 0.118.0 \
  --service-scope auto \
  --default-model gpt-5.4 \
  --default-cd /srv/app \
  --sync-local-on-up \
  --api-key-env OPENAI_API_KEY \
  --auto-login-on-up

codex-remote init prod \
  --ssh-config-host llm.ezbuilder.app \
  --remote-root /srv/app \
  --codex-version 0.118.0 \
  --service-scope auto \
  --default-model gpt-5.4 \
  --default-cd /srv/app \
  --sync-local-on-up \
  --api-key-env OPENAI_API_KEY \
  --auto-login-on-up \
  --launch -- -m gpt-5.4

codex-remote profile save prod \
  --ssh-config-host llm.ezbuilder.app \
  --remote-root /srv/app \
  --codex-version 0.118.0 \
  --service-scope auto \
  --codex-app-server-port 4500

codex-remote profile list
codex-remote profile list --json
codex-remote profile current
codex-remote profile current --json
codex-remote profile use prod
codex-remote profile aliases
codex-remote profile aliases --all
codex-remote profile aliases --json
codex-remote profile import-ssh-config prod \
  --remote-root /srv/app
codex-remote profile import-ssh-config prod \
  --alias llm.ezbuilder.app \
  --remote-root /srv/app
codex-remote connect llm.ezbuilder.app \
  --name prod \
  --remote-root /srv/app
codex-remote profile show prod
codex-remote profile show prod --json
codex-remote profile delete old-prod
```

`profile aliases` shows resolved `host`, `user`, `port`, and whether an `IdentityFile` is configured. By default it hides duplicate raw IP aliases when a friendly alias exists on the same `Host` line. Add `--all` to include everything from `~/.ssh/config`.
`profile import-ssh-config` will use `--alias` when provided, otherwise it treats the profile name itself as the SSH alias.
The first saved profile becomes the default automatically. You can inspect or switch it with `profile current` and `profile use`.
`connect` is the shortest first-run path: it reuses an existing profile when one exists, otherwise imports the SSH alias, sets that profile as default, runs `up`, and launches Codex unless you pass `--no-launch`.
Profile saves now validate ports, environment variable names, and remote paths up front. Invalid on-disk profiles are skipped on load instead of poisoning the whole store.
Use `profile doctor` to inspect skipped entries and `profile doctor --rewrite` to rewrite `profiles.json` with only the valid subset when you want to clean up a damaged store.

`init` is the shortest path for first-time setup: it saves the profile and immediately runs the same orchestration as `up`. Add `--launch -- ...` if you want it to attach to local Codex as soon as setup finishes.
Saved `default-model` and `default-cd` values are applied automatically by `init --launch`, `up --launch`, and `open` unless you override them after `--`.
Saved `--sync-local-on-up` and `--auto-login-on-up` values are also reused by later `up` and `open` runs, so repeat sessions stay one-command.

Start or inspect the remote app-server:

```bash
codex-remote up prod
codex-remote up --json
codex-remote up prod --sync-local
codex-remote up prod --launch -- -m gpt-5.4
codex-remote open prod
codex-remote open
codex-remote bootstrap prod
codex-remote start prod
codex-remote status --json
codex-remote smoke prod
codex-remote doctor prod
codex-remote doctor --json --strict
codex-remote support-bundle prod --json
codex-remote logs --json
codex-remote logs prod --lines 100
codex-remote stop prod
codex-remote auth-login prod
```

`bootstrap` installs the remote helper script once under `~/.local/bin/codex-remote-app-server`.
`start`, `status`, `smoke`, `doctor`, `logs`, and `launch` will also auto-bootstrap it on first use.
`up` is the one-shot production bootstrap: it makes sure the pinned remote Codex version is present, prefers a systemd service when available, starts the runtime, and finishes with a full `doctor` pass. Add `--sync-local` if you want it to also align your local Codex CLI to the remote version before attaching.
`open` is the shortest repeat-use path: it reuses the saved profile defaults for model, remote working directory, and sync policy, then runs `up + launch`.
When a default profile is configured, `open`, `up`, `status`, `doctor`, `logs`, and the other profile-bound commands can omit the profile name entirely.
Most operational commands also support `--json` now, so shell scripts or CI jobs can consume structured output. `doctor --strict` and `up --strict` return a non-zero exit code when a warning such as auth-missing or local/remote version mismatch is present.
`auth-login` lets you push a local API key into the remote Codex CLI login flow on demand. If the profile was saved with `--auto-login-on-up`, `up` and `open` will do the same thing automatically whenever the remote runtime reports `logged-out`.
`support-bundle` writes a local `.tar.gz` with profile metadata, profile-store warnings, remote `status`, `doctor`, and recent logs. Even if the remote host is partially broken, the bundle still gets created and records which collection steps failed. By default it redacts tokens, API keys, bearer headers, and private-key blocks inside captured payloads and logs; pass `--no-redact` only when you explicitly need raw output.

`doctor` warns when local and remote Codex versions differ, because `--remote` compatibility is safest when both sides stay aligned. It also reports whether the remote Codex CLI is already logged in.

Install or upgrade Codex on the remote host:

```bash
codex-remote install-codex prod
codex-remote install-codex prod --version 0.118.0
codex-remote upgrade-codex prod
```

If the remote app-server is already running as a managed service or ad-hoc process, these commands will restart that runtime so the new Codex binary is used immediately.

If the VPS runs systemd and you want the remote app-server supervised as a real service:

```bash
codex-remote service-install prod
codex-remote service-status prod
codex-remote service-start prod
codex-remote service-stop prod
codex-remote service-uninstall prod
```

Use `--service-scope system` for root-managed units under `/etc/systemd/system`, `--service-scope user` for rootless units under `~/.config/systemd/user`, or leave the default `auto` to pick `system` for root and `user` otherwise.
When that service is installed, `status`, `smoke`, `doctor`, `stop`, and `launch` will prefer the managed service runtime over the ad-hoc `nohup` process runtime.

Launch local Codex against the remote app-server:

```bash
codex-remote launch prod -- -m gpt-5.4
```

`launch` automatically:

1. Starts `codex app-server --listen ws://127.0.0.1:<port>` on the VPS
2. Opens `ssh -L 127.0.0.1:<local>:127.0.0.1:<remote>` locally
3. Runs local `codex --remote ws://127.0.0.1:<local>`
4. Forwards `remote_root` as the default remote workspace via `-C`

If you want a different remote workspace for one session, pass your own `-C` after `--`:

```bash
codex-remote launch prod -- -C /srv/app-next
```

## First use

Example profile using SSH config:

```text
ssh_profile_save(
  name="prod",
  ssh_config_host="prod",
  remote_root="/srv/app",
  auth_mode="ssh_config",
  allowed_exec_prefixes=["git status", "systemctl status myapp"],
  allowed_read_roots=["config", "logs"],
  allowed_write_roots=["releases"]
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
  password_storage="session_only",
  allow_connect_without_confirmation=true
)
```

High-impact actions now require either `confirm=true` or a matching profile allowlist. The plugin also stores profile metadata with restrictive file permissions (`0700` state dir, `0600` `profiles.json`) and only reports whether a key path is configured instead of echoing the actual path.

Then connect and work:

```text
ssh_connect(profile_name="prod", confirm=true)
ssh_exec(profile_name="prod", command="git status")
ssh_read_file(profile_name="prod", remote_path="README.md")
ssh_write_file(
  profile_name="prod",
  remote_path=".env.example",
  content="...",
  confirm=true
)
ssh_upload(
  profile_name="prod",
  local_path="./dist",
  remote_path="releases/dist"
)
ssh_sync(
  profile_name="prod",
  local_path="./src",
  remote_path="releases/app-src",
  direction="upload"
)
ssh_disconnect(profile_name="prod")
```

## Repository publish flow

```bash
cd ~/workspace/ssh-remote-control
git init
git add .
git commit -m "feat: add ssh remote control Codex plugin"
gh repo create ezBuilder/ssh-remote-control --public --source=. --push
```

If you publish under a different GitHub owner or repository name, update `.codex-plugin/plugin.json`.

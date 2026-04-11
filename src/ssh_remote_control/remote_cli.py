from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Callable

from paramiko.config import SSHConfig

from ssh_remote_control.models import AuthMode
from ssh_remote_control.models import SSHProfile
from ssh_remote_control.models import PasswordStorage
from ssh_remote_control.profile_store import ProfileStore
from ssh_remote_control.remote_codex import RemoteCodexManager


def codex_home() -> Path:
    env_override = os.environ.get("CODEX_HOME")
    if env_override:
        return Path(env_override).expanduser()
    return Path.home() / ".codex"


STATE_ROOT = codex_home() / "ssh-remote-control"
PROFILES_PATH = STATE_ROOT / "profiles.json"
DEFAULT_SSH_CONFIG_PATH = Path.home() / ".ssh" / "config"
MANAGED_RELEASE_GLOBS = (
    "ssh_remote_control-*.whl",
    "ssh_remote_control-*.tar.gz",
    "ssh-remote-control-plugin*.zip",
    "SHA256SUMS",
    "release-manifest.json",
)
SUPPORT_BUNDLE_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        "[REDACTED PRIVATE KEY]",
    ),
    (re.compile(r"\bsk-[A-Za-z0-9_-]+\b"), "sk-REDACTED"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._-]+\b"), "Bearer REDACTED"),
    (
        re.compile(r"(?im)\b([A-Z0-9_]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD))\s*=\s*(\S+)\b"),
        r"\1=REDACTED",
    ),
    (
        re.compile(r"(?im)\b(api[_-]?key|token|secret|password|authorization)\s*[:=]\s*([^\r\n]+)"),
        r"\1=REDACTED",
    ),
)
PROFILE_NAME_COMMANDS = {
    "launch",
    "open",
    "start",
    "status",
    "stop",
    "smoke",
    "up",
    "bootstrap",
    "install-codex",
    "upgrade-codex",
    "auth-login",
    "service-install",
    "service-status",
    "service-start",
    "service-stop",
    "service-uninstall",
    "doctor",
    "logs",
}


def _profile_store() -> ProfileStore:
    return ProfileStore(PROFILES_PATH)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_ssh_config(path: Path) -> SSHConfig:
    if not path.exists():
        raise SystemExit(f"ssh config not found: {path}")
    config = SSHConfig()
    with path.open() as handle:
        config.parse(handle)
    return config


def _build_ssh_config_profile(
    *,
    alias: str,
    profile_name: str,
    remote_root: str,
    config_path: Path,
    codex_binary: str = "codex",
    codex_version: str | None = None,
    codex_app_server_port: int = 4500,
    service_scope: str = "auto",
    default_model: str | None = None,
    default_cd: str | None = None,
    sync_local_on_up: bool = False,
    api_key_env: str | None = None,
    auto_login_on_up: bool = False,
) -> SSHProfile:
    config = _load_ssh_config(config_path)
    resolved = config.lookup(alias)
    hostname = resolved.get("hostname")
    if not hostname:
        raise SystemExit(f"unknown ssh config alias: {alias}")
    username = resolved.get("user")
    port = int(resolved.get("port", 22))
    return SSHProfile(
        name=profile_name,
        host=str(hostname),
        port=port,
        username=str(username) if username else None,
        remote_root=remote_root,
        codex_binary=codex_binary,
        codex_version=codex_version,
        codex_app_server_port=codex_app_server_port,
        service_scope=service_scope,
        default_model=default_model,
        default_cd=default_cd,
        sync_local_on_up=sync_local_on_up,
        api_key_env=api_key_env,
        auto_login_on_up=auto_login_on_up,
        auth_mode=AuthMode.SSH_CONFIG,
        password_storage=PasswordStorage.NEVER,
        ssh_config_host=alias,
        key_path=None,
    )


def _is_ip_literal(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _list_ssh_config_entries(path: Path, *, include_all: bool = False) -> list[dict[str, object]]:
    config = _load_ssh_config(path)
    entries: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw_line in path.read_text().splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if not parts or parts[0].lower() != "host":
            continue
        aliases = [alias for alias in parts[1:] if not any(char in alias for char in "*?!")]
        has_named_alias = any(not _is_ip_literal(alias) for alias in aliases)
        for alias in aliases:
            if not include_all and has_named_alias and _is_ip_literal(alias):
                continue
            if alias in seen:
                continue
            resolved = config.lookup(alias)
            entries.append(
                {
                    "alias": alias,
                    "hostname": resolved.get("hostname", alias),
                    "user": resolved.get("user"),
                    "port": int(resolved.get("port", 22)),
                    "identity_file_configured": bool(resolved.get("identityfile")),
                }
            )
            seen.add(alias)
    return entries


def _completion_profile_names() -> list[str]:
    try:
        return sorted(profile.name for profile in _profile_store().list_profiles())
    except Exception:
        return []


def _completion_ssh_aliases(path: Path = DEFAULT_SSH_CONFIG_PATH) -> list[str]:
    try:
        entries = _list_ssh_config_entries(path)
    except SystemExit:
        return []
    return sorted(str(entry["alias"]) for entry in entries if entry.get("alias"))


def _parser_subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return {
                name: subparser
                for name, subparser in action.choices.items()
                if not name.startswith("_")
            }
    return {}


def _parser_option_actions(parser: argparse.ArgumentParser) -> dict[str, argparse.Action]:
    options: dict[str, argparse.Action] = {}
    for action in parser._actions:
        for option in action.option_strings:
            options[option] = action
    return options


def _action_expects_value(action: argparse.Action) -> bool:
    return bool(action.option_strings) and action.nargs != 0


def _completion_candidates_for_action(action: argparse.Action) -> list[str]:
    if action.choices is not None:
        return [str(choice) for choice in action.choices]
    option_strings = set(action.option_strings)
    if option_strings & {"--ssh-config-host", "--alias"}:
        return _completion_ssh_aliases()
    if option_strings & {"--name"}:
        return _completion_profile_names()
    return []


def _resolve_completion_context(
    previous_tokens: list[str],
) -> tuple[argparse.ArgumentParser, tuple[str, ...], list[str], argparse.Action | None, bool]:
    parser = _build_parser()
    current_parser = parser
    command_path: list[str] = []
    positionals_consumed: list[str] = []
    expecting_value_for: argparse.Action | None = None
    passthrough = False
    index = 0

    while index < len(previous_tokens):
        token = previous_tokens[index]
        if token == "--":
            passthrough = True
            break
        if expecting_value_for is not None:
            expecting_value_for = None
            index += 1
            continue

        subcommands = _parser_subcommands(current_parser)
        if token in subcommands:
            current_parser = subcommands[token]
            command_path.append(token)
            positionals_consumed = []
            index += 1
            continue

        option_actions = _parser_option_actions(current_parser)
        option_name = token.split("=", 1)[0] if token.startswith("--") and "=" in token else token
        action = option_actions.get(option_name)
        if action is not None:
            if _action_expects_value(action) and "=" not in token:
                expecting_value_for = action
            index += 1
            continue

        if token.startswith("-"):
            index += 1
            continue

        positionals_consumed.append(token)
        index += 1

    return current_parser, tuple(command_path), positionals_consumed, expecting_value_for, passthrough


def _completion_candidates_for_position(
    command_path: tuple[str, ...],
    positionals_consumed: list[str],
) -> list[str]:
    if not command_path:
        return []

    if command_path[0] == "profile":
        if len(command_path) < 2:
            return []
        if command_path[1] in {"show", "delete", "use"} and not positionals_consumed:
            return _completion_profile_names()
        return []

    command = command_path[0]
    if command == "connect" and not positionals_consumed:
        return sorted(set(_completion_profile_names()) | set(_completion_ssh_aliases()))
    if command in PROFILE_NAME_COMMANDS and not positionals_consumed:
        return _completion_profile_names()
    return []


def _complete_tokens(previous_tokens: list[str], current: str) -> list[str]:
    parser, command_path, positionals_consumed, expecting_value_for, passthrough = _resolve_completion_context(previous_tokens)
    if passthrough:
        return []

    if expecting_value_for is not None:
        candidates = _completion_candidates_for_action(expecting_value_for)
    elif current.startswith("-"):
        candidates = list(_parser_option_actions(parser).keys())
    else:
        candidates: list[str] = []
        subcommands = _parser_subcommands(parser)
        if not command_path:
            candidates.extend(subcommands.keys())
        elif command_path == ("profile",):
            candidates.extend(subcommands.keys())
        candidates.extend(_completion_candidates_for_position(command_path, positionals_consumed))
        candidates.extend(_parser_option_actions(parser).keys())

    filtered = [candidate for candidate in candidates if candidate.startswith(current)]
    return sorted(dict.fromkeys(filtered))


def _render_completion_script(shell: str) -> str:
    if shell == "bash":
        return """_codex_remote_complete() {
  local cur
  cur="${COMP_WORDS[COMP_CWORD]}"
  local args=()
  local i
  for ((i=1; i<COMP_CWORD; i++)); do
    args+=("${COMP_WORDS[i]}")
  done
  local suggestions
  suggestions="$(codex-remote _complete --current "$cur" -- "${args[@]}")"
  COMPREPLY=()
  if [[ -n "$suggestions" ]]; then
    while IFS= read -r line; do
      COMPREPLY+=("$line")
    done <<< "$suggestions"
  fi
}
complete -F _codex_remote_complete codex-remote
"""
    if shell == "zsh":
        return """#compdef codex-remote
_codex_remote() {
  local current_word
  current_word="${words[CURRENT]}"
  local -a args
  if (( CURRENT > 2 )); then
    args=("${words[@]:2:$((CURRENT-2))}")
  else
    args=()
  fi
  local -a suggestions
  suggestions=("${(@f)$(codex-remote _complete --current "$current_word" -- "${args[@]}")}")
  compadd -a suggestions
}
compdef _codex_remote codex-remote
"""
    if shell == "fish":
        return """function __codex_remote_complete
    set -l tokens (commandline -opc)
    set -e tokens[1]
    set -l current (commandline -ct)
    codex-remote _complete --current \"$current\" -- $tokens
end
complete -c codex-remote -f -a '(__codex_remote_complete)'
"""
    raise SystemExit(f"unsupported shell completion target: {shell}")


def _completion_install_target(shell: str) -> Path:
    if shell == "bash":
        return Path.home() / ".local" / "share" / "bash-completion" / "completions" / "codex-remote"
    if shell == "zsh":
        return Path.home() / ".zfunc" / "_codex-remote"
    if shell == "fish":
        return Path.home() / ".config" / "fish" / "completions" / "codex-remote.fish"
    raise SystemExit(f"unsupported shell completion target: {shell}")


def _completion_activation_hint(shell: str, target: Path) -> str:
    if shell == "zsh":
        return f"ensure {target.parent} is in fpath and run compinit, or restart your shell"
    if shell == "bash":
        return f"restart your shell or source {target}"
    if shell == "fish":
        return "restart fish or run `functions -e __codex_remote_complete` before retrying completion"
    return "restart your shell"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codex-remote",
        description="Launch local Codex against a remote Codex app-server over SSH.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    profile_parser = subparsers.add_parser("profile", help="Manage saved remote profiles.")
    profile_subparsers = profile_parser.add_subparsers(dest="profile_command", required=True)

    profile_save = profile_subparsers.add_parser("save", help="Save or update a remote profile.")
    profile_save.add_argument("name")
    profile_save.add_argument("--host")
    profile_save.add_argument("--ssh-config-host")
    profile_save.add_argument("--port", type=int, default=22)
    profile_save.add_argument("--username")
    profile_save.add_argument("--remote-root", required=True)
    profile_save.add_argument("--codex-binary", default="codex")
    profile_save.add_argument("--codex-version")
    profile_save.add_argument("--codex-app-server-port", type=int, default=4500)
    profile_save.add_argument("--service-scope", choices=["auto", "system", "user"], default="auto")
    profile_save.add_argument("--default-model")
    profile_save.add_argument("--default-cd")
    profile_save.add_argument("--sync-local-on-up", action="store_true")
    profile_save.add_argument("--api-key-env")
    profile_save.add_argument("--auto-login-on-up", action="store_true")
    profile_save.add_argument(
        "--auth-mode",
        choices=[mode.value for mode in AuthMode],
        default=AuthMode.SSH_CONFIG.value,
    )
    profile_save.add_argument(
        "--password-storage",
        choices=[mode.value for mode in PasswordStorage],
        default=PasswordStorage.NEVER.value,
    )
    profile_save.add_argument("--key-path")

    profile_list = profile_subparsers.add_parser("list", help="List saved remote profiles.")
    profile_list.add_argument("--json", action="store_true")

    profile_current = profile_subparsers.add_parser(
        "current",
        help="Show the current default profile.",
    )
    profile_current.add_argument("--json", action="store_true")

    profile_aliases = profile_subparsers.add_parser(
        "aliases",
        help="List host aliases from an SSH config file.",
    )
    profile_aliases.add_argument(
        "--ssh-config-path",
        default=str(DEFAULT_SSH_CONFIG_PATH),
    )
    profile_aliases.add_argument("--all", action="store_true")
    profile_aliases.add_argument("--json", action="store_true")

    profile_show = profile_subparsers.add_parser("show", help="Show one saved remote profile.")
    profile_show.add_argument("name")
    profile_show.add_argument("--json", action="store_true")

    profile_doctor = profile_subparsers.add_parser(
        "doctor",
        help="Inspect the saved profile store for invalid or skipped entries.",
    )
    profile_doctor.add_argument("--json", action="store_true")
    profile_doctor.add_argument("--rewrite", action="store_true")

    profile_delete = profile_subparsers.add_parser("delete", help="Delete a saved remote profile.")
    profile_delete.add_argument("name")

    profile_use = profile_subparsers.add_parser(
        "use",
        help="Set the default profile used when no profile name is provided.",
    )
    profile_use.add_argument("name")

    profile_import = profile_subparsers.add_parser(
        "import-ssh-config",
        help="Create or update a profile from an SSH config alias.",
    )
    profile_import.add_argument("name")
    profile_import.add_argument("--alias")
    profile_import.add_argument("--remote-root", required=True)
    profile_import.add_argument("--codex-binary", default="codex")
    profile_import.add_argument("--codex-version")
    profile_import.add_argument("--codex-app-server-port", type=int, default=4500)
    profile_import.add_argument("--service-scope", choices=["auto", "system", "user"], default="auto")
    profile_import.add_argument("--default-model")
    profile_import.add_argument("--default-cd")
    profile_import.add_argument("--sync-local-on-up", action="store_true")
    profile_import.add_argument("--api-key-env")
    profile_import.add_argument("--auto-login-on-up", action="store_true")
    profile_import.add_argument(
        "--ssh-config-path",
        default=str(DEFAULT_SSH_CONFIG_PATH),
    )

    init_parser = subparsers.add_parser(
        "init",
        help="Save a profile and immediately prepare the remote Codex runtime.",
    )
    init_parser.add_argument("name")
    init_parser.add_argument("--host")
    init_parser.add_argument("--ssh-config-host")
    init_parser.add_argument("--port", type=int, default=22)
    init_parser.add_argument("--username")
    init_parser.add_argument("--remote-root", required=True)
    init_parser.add_argument("--codex-binary", default="codex")
    init_parser.add_argument("--codex-version")
    init_parser.add_argument("--codex-app-server-port", type=int, default=4500)
    init_parser.add_argument("--service-scope", choices=["auto", "system", "user"], default="auto")
    init_parser.add_argument("--default-model")
    init_parser.add_argument("--default-cd")
    init_parser.add_argument("--sync-local-on-up", action="store_true")
    init_parser.add_argument("--api-key-env")
    init_parser.add_argument("--auto-login-on-up", action="store_true")
    init_parser.add_argument("--local-port", type=int)
    init_parser.add_argument("--sync-local", action="store_true")
    init_parser.add_argument("--launch", action="store_true")
    init_parser.add_argument(
        "--auth-mode",
        choices=[mode.value for mode in AuthMode],
        default=AuthMode.SSH_CONFIG.value,
    )
    init_parser.add_argument(
        "--password-storage",
        choices=[mode.value for mode in PasswordStorage],
        default=PasswordStorage.NEVER.value,
    )
    init_parser.add_argument("--key-path")

    connect_parser = subparsers.add_parser(
        "connect",
        help="Use an existing profile or import an SSH alias, set it as default, prepare the runtime, and optionally launch Codex.",
    )
    connect_parser.add_argument("target")
    connect_parser.add_argument("--name")
    connect_parser.add_argument("--remote-root")
    connect_parser.add_argument("--ssh-config-path", default=str(DEFAULT_SSH_CONFIG_PATH))
    connect_parser.add_argument("--codex-binary", default="codex")
    connect_parser.add_argument("--codex-version")
    connect_parser.add_argument("--codex-app-server-port", type=int, default=4500)
    connect_parser.add_argument("--service-scope", choices=["auto", "system", "user"], default="auto")
    connect_parser.add_argument("--default-model")
    connect_parser.add_argument("--default-cd")
    connect_parser.add_argument("--sync-local-on-up", action="store_true")
    connect_parser.add_argument("--api-key-env")
    connect_parser.add_argument("--auto-login-on-up", action="store_true")
    connect_parser.add_argument("--no-default", action="store_true")
    connect_parser.add_argument("--no-launch", action="store_true")
    connect_parser.add_argument("--local-port", type=int)
    connect_parser.add_argument("--sync-local", action="store_true")
    connect_parser.add_argument("--json", action="store_true")
    connect_parser.add_argument("--strict", action="store_true")

    launch_parser = subparsers.add_parser("launch", help="Start remote app-server and open Codex.")
    launch_parser.add_argument("profile_name", nargs="?")
    launch_parser.add_argument("--local-port", type=int)
    launch_parser.add_argument("--local-codex-binary", default="codex")

    open_parser = subparsers.add_parser(
        "open",
        help="Use profile defaults to prepare the remote runtime and launch local Codex.",
    )
    open_parser.add_argument("profile_name", nargs="?")
    open_parser.add_argument("--local-port", type=int)

    start_parser = subparsers.add_parser("start", help="Start the remote app-server only.")
    start_parser.add_argument("profile_name", nargs="?")
    start_parser.add_argument("--json", action="store_true")

    status_parser = subparsers.add_parser("status", help="Show remote app-server status.")
    status_parser.add_argument("profile_name", nargs="?")
    status_parser.add_argument("--json", action="store_true")

    stop_parser = subparsers.add_parser("stop", help="Stop the remote app-server.")
    stop_parser.add_argument("profile_name", nargs="?")
    stop_parser.add_argument("--json", action="store_true")

    smoke_parser = subparsers.add_parser(
        "smoke",
        help="Start the remote app-server if needed and verify websocket reachability through an SSH tunnel.",
    )
    smoke_parser.add_argument("profile_name", nargs="?")
    smoke_parser.add_argument("--local-port", type=int)
    smoke_parser.add_argument("--json", action="store_true")

    up_parser = subparsers.add_parser(
        "up",
        help="Prepare the remote Codex runtime, start the preferred runtime, and run diagnostics.",
    )
    up_parser.add_argument("profile_name", nargs="?")
    up_parser.add_argument("--local-port", type=int)
    up_parser.add_argument("--sync-local", action="store_true")
    up_parser.add_argument("--launch", action="store_true")
    up_parser.add_argument("--json", action="store_true")
    up_parser.add_argument("--strict", action="store_true")

    bootstrap_parser = subparsers.add_parser(
        "bootstrap",
        help="Install or refresh the remote Codex helper script on the target host.",
    )
    bootstrap_parser.add_argument("profile_name", nargs="?")
    bootstrap_parser.add_argument("--json", action="store_true")

    install_codex_parser = subparsers.add_parser(
        "install-codex",
        help="Install Codex CLI on the remote host and restart the active runtime if needed.",
    )
    install_codex_parser.add_argument("profile_name", nargs="?")
    install_codex_parser.add_argument("--version")
    install_codex_parser.add_argument("--json", action="store_true")

    upgrade_codex_parser = subparsers.add_parser(
        "upgrade-codex",
        help="Upgrade Codex CLI on the remote host and restart the active runtime if needed.",
    )
    upgrade_codex_parser.add_argument("profile_name", nargs="?")
    upgrade_codex_parser.add_argument("--json", action="store_true")

    auth_login_parser = subparsers.add_parser(
        "auth-login",
        help="Log the remote Codex CLI in using an API key from a local environment variable.",
    )
    auth_login_parser.add_argument("profile_name", nargs="?")
    auth_login_parser.add_argument("--api-key-env")
    auth_login_parser.add_argument("--json", action="store_true")

    service_install_parser = subparsers.add_parser(
        "service-install",
        help="Install and enable a systemd service for the remote Codex app-server.",
    )
    service_install_parser.add_argument("profile_name", nargs="?")
    service_install_parser.add_argument("--json", action="store_true")

    service_status_parser = subparsers.add_parser(
        "service-status",
        help="Show the remote Codex systemd service status.",
    )
    service_status_parser.add_argument("profile_name", nargs="?")
    service_status_parser.add_argument("--json", action="store_true")

    service_start_parser = subparsers.add_parser(
        "service-start",
        help="Start the remote Codex systemd service.",
    )
    service_start_parser.add_argument("profile_name", nargs="?")
    service_start_parser.add_argument("--json", action="store_true")

    service_stop_parser = subparsers.add_parser(
        "service-stop",
        help="Stop the remote Codex systemd service.",
    )
    service_stop_parser.add_argument("profile_name", nargs="?")
    service_stop_parser.add_argument("--json", action="store_true")

    service_uninstall_parser = subparsers.add_parser(
        "service-uninstall",
        help="Disable and remove the remote Codex systemd service.",
    )
    service_uninstall_parser.add_argument("profile_name", nargs="?")
    service_uninstall_parser.add_argument("--json", action="store_true")

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Run local Codex, remote Codex, and websocket attach diagnostics for a profile.",
    )
    doctor_parser.add_argument("profile_name", nargs="?")
    doctor_parser.add_argument("--local-port", type=int)
    doctor_parser.add_argument("--json", action="store_true")
    doctor_parser.add_argument("--strict", action="store_true")

    logs_parser = subparsers.add_parser(
        "logs",
        help="Read the remote Codex app-server log for a profile.",
    )
    logs_parser.add_argument("profile_name", nargs="?")
    logs_parser.add_argument("--lines", type=int, default=200)
    logs_parser.add_argument("--json", action="store_true")

    install_cli_parser = subparsers.add_parser(
        "install-cli",
        help="Install a global codex-remote launcher into a bin directory.",
    )
    install_cli_parser.add_argument(
        "--bin-dir",
        default=str(Path.home() / ".local" / "bin"),
    )
    install_cli_parser.add_argument(
        "--shell-completion",
        choices=["bash", "zsh", "fish"],
    )
    install_cli_parser.add_argument("--json", action="store_true")

    completion_parser = subparsers.add_parser(
        "completion",
        help="Print a shell completion script.",
    )
    completion_parser.add_argument("shell", choices=["bash", "zsh", "fish"])

    support_bundle_parser = subparsers.add_parser(
        "support-bundle",
        help="Collect profile, diagnostics, and recent logs into a local tar.gz bundle.",
    )
    support_bundle_parser.add_argument("profile_name", nargs="?")
    support_bundle_parser.add_argument("--local-port", type=int)
    support_bundle_parser.add_argument("--lines", type=int, default=200)
    support_bundle_parser.add_argument("--output")
    support_bundle_parser.add_argument("--no-redact", action="store_true")
    support_bundle_parser.add_argument("--json", action="store_true")

    hidden_complete_parser = subparsers.add_parser("_complete", help=argparse.SUPPRESS)
    hidden_complete_parser.add_argument("--current", default="")
    hidden_complete_parser.add_argument("tokens", nargs=argparse.REMAINDER)

    package_parser = subparsers.add_parser(
        "package",
        help="Run release packaging checks from a source checkout: tests, python -m build, and plugin bundle.",
    )
    package_parser.add_argument("--skip-tests", action="store_true")
    package_parser.add_argument("--skip-build", action="store_true")
    package_parser.add_argument("--skip-bundle", action="store_true")
    package_parser.add_argument("--no-clean", action="store_true")
    package_parser.add_argument("--strict-release", action="store_true")
    package_parser.add_argument("--expected-version")
    package_parser.add_argument("--bundle-output")
    package_parser.add_argument("--json", action="store_true")

    return parser


def _require_profile(name: str | None) -> SSHProfile:
    store = _profile_store()
    profile = store.get_profile(name) if name else store.get_default_profile()
    if profile is None:
        if name is None:
            raise SystemExit("no profile specified and no default profile configured")
        raise SystemExit(f"unknown profile: {name}")
    return profile


def _cmd_profile_save(args: argparse.Namespace) -> int:
    profile = SSHProfile(
        name=args.name,
        host=args.host or args.ssh_config_host or args.name,
        port=args.port,
        username=args.username,
        remote_root=args.remote_root,
        codex_binary=args.codex_binary,
        codex_version=args.codex_version,
        codex_app_server_port=args.codex_app_server_port,
        service_scope=args.service_scope,
        default_model=args.default_model,
        default_cd=args.default_cd,
        sync_local_on_up=args.sync_local_on_up,
        api_key_env=args.api_key_env,
        auto_login_on_up=args.auto_login_on_up,
        auth_mode=AuthMode(args.auth_mode),
        password_storage=PasswordStorage(args.password_storage),
        ssh_config_host=args.ssh_config_host,
        key_path=args.key_path,
    )
    _profile_store().save_profile(profile)
    print(f"saved profile {profile.name}")
    return 0


def _cmd_profile_import_ssh_config(args: argparse.Namespace) -> int:
    config_path = Path(args.ssh_config_path).expanduser()
    alias = args.alias or args.name
    profile = _build_ssh_config_profile(
        alias=alias,
        profile_name=args.name,
        remote_root=args.remote_root,
        config_path=config_path,
        codex_binary=args.codex_binary,
        codex_version=args.codex_version,
        codex_app_server_port=args.codex_app_server_port,
        service_scope=args.service_scope,
        default_model=args.default_model,
        default_cd=args.default_cd,
        sync_local_on_up=args.sync_local_on_up,
        api_key_env=args.api_key_env,
        auto_login_on_up=args.auto_login_on_up,
    )
    _profile_store().save_profile(profile)
    print(f"saved profile {profile.name} from ssh alias {alias}")
    return 0


def _profile_payload(profile: SSHProfile) -> dict[str, object]:
    return {
        "name": profile.name,
        "host": profile.host,
        "ssh_config_host": profile.ssh_config_host,
        "port": profile.port,
        "username": profile.username,
        "remote_root": profile.remote_root,
        "codex_binary": profile.codex_binary,
        "codex_version": profile.codex_version,
        "codex_app_server_port": profile.codex_app_server_port,
        "service_scope": profile.service_scope,
        "default_model": profile.default_model,
        "default_cd": profile.default_cd,
        "sync_local_on_up": profile.sync_local_on_up,
        "api_key_env": profile.api_key_env,
        "auto_login_on_up": profile.auto_login_on_up,
        "auth_mode": profile.auth_mode.value,
        "password_storage": profile.password_storage.value,
        "key_path_configured": bool(profile.key_path),
        "allow_connect_without_confirmation": profile.allow_connect_without_confirmation,
        "allowed_exec_prefixes": list(profile.allowed_exec_prefixes),
        "allowed_read_roots": list(profile.allowed_read_roots),
        "allowed_write_roots": list(profile.allowed_write_roots),
    }


def _cmd_profile_list(*, as_json: bool = False) -> int:
    store = _profile_store()
    default_name = store.get_default_profile_name()
    profiles = store.list_profiles()
    if as_json:
        print(
            json.dumps(
                [
                    {
                        **_profile_payload(profile),
                        "is_default": profile.name == default_name,
                    }
                    for profile in profiles
                ],
                indent=2,
            )
        )
        return 0
    for profile in profiles:
        alias = profile.ssh_config_host or "-"
        print(
            f"{profile.name}\tdefault={str(profile.name == default_name).lower()}"
            f"\thost={profile.host}\talias={alias}\troot={profile.remote_root or '-'}"
            f"\tport={profile.codex_app_server_port}\tbinary={profile.codex_binary}"
            f"\tversion={profile.codex_version or 'latest'}"
            f"\tscope={profile.service_scope}"
            f"\tmodel={profile.default_model or '-'}"
            f"\tcd={profile.default_cd or '-'}"
            f"\tsync_local={str(profile.sync_local_on_up).lower()}"
            f"\tauto_login={str(profile.auto_login_on_up).lower()}"
            f"\tapi_key_env={profile.api_key_env or '-'}"
        )
    return 0


def _cmd_profile_current(*, as_json: bool = False) -> int:
    store = _profile_store()
    profile = store.get_default_profile()
    if profile is None:
        raise SystemExit("no default profile configured")
    payload = {**_profile_payload(profile), "is_default": True}
    if as_json:
        print(json.dumps(payload, indent=2))
        return 0
    for key, value in payload.items():
        print(f"{key}={value}")
    return 0


def _cmd_profile_aliases(args: argparse.Namespace) -> int:
    config_path = Path(args.ssh_config_path).expanduser()
    entries = _list_ssh_config_entries(config_path, include_all=args.all)
    if args.json:
        print(json.dumps(entries, indent=2))
        return 0
    for entry in entries:
        print(
            f"{entry['alias']}\thost={entry['hostname']}\tuser={entry['user'] or '-'}"
            f"\tport={entry['port']}\tidentity_file={str(entry['identity_file_configured']).lower()}"
        )
    return 0


def _cmd_profile_show(args: argparse.Namespace) -> int:
    profile = _require_profile(args.name)
    payload = {
        **_profile_payload(profile),
        "is_default": profile.name == _profile_store().get_default_profile_name(),
    }
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    for key, value in payload.items():
        print(f"{key}={value}")
    return 0


def _cmd_profile_doctor(args: argparse.Namespace) -> int:
    store = _profile_store()
    warnings = store.get_load_warnings()
    rewritten = False
    if args.rewrite and warnings:
        store.rewrite()
        warnings = []
        rewritten = True
    payload = {
        "status": "rewritten" if rewritten else ("warning" if warnings else "ok"),
        "path": str(store.path),
        "profile_count": len(store.list_profiles()),
        "default_profile": store.get_default_profile_name(),
        "invalid_profile_count": len(warnings),
        "warnings": warnings,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    _print_status_values(
        payload,
        ["status", "path", "profile_count", "default_profile", "invalid_profile_count"],
    )
    for warning in warnings:
        print(f"warning={warning['name']}: {warning['error']}")
    return 0


def _cmd_profile_delete(args: argparse.Namespace) -> int:
    if not _profile_store().delete_profile(args.name):
        raise SystemExit(f"unknown profile: {args.name}")
    print(f"deleted profile {args.name}")
    return 0


def _cmd_profile_use(args: argparse.Namespace) -> int:
    store = _profile_store()
    try:
        profile = store.set_default_profile(args.name)
    except KeyError:
        raise SystemExit(f"unknown profile: {args.name}") from None
    print(f"default profile={profile.name}")
    return 0


def _cmd_init(args: argparse.Namespace) -> int:
    manager = RemoteCodexManager()
    profile = SSHProfile(
        name=args.name,
        host=args.host or args.ssh_config_host or args.name,
        port=args.port,
        username=args.username,
        remote_root=args.remote_root,
        codex_binary=args.codex_binary,
        codex_version=args.codex_version,
        codex_app_server_port=args.codex_app_server_port,
        service_scope=args.service_scope,
        default_model=args.default_model,
        default_cd=args.default_cd,
        sync_local_on_up=args.sync_local_on_up,
        api_key_env=args.api_key_env,
        auto_login_on_up=args.auto_login_on_up,
        auth_mode=AuthMode(args.auth_mode),
        password_storage=PasswordStorage(args.password_storage),
        ssh_config_host=args.ssh_config_host,
        key_path=args.key_path,
    )
    _profile_store().save_profile(profile)
    result = manager.up(
        profile,
        local_port=args.local_port,
        sync_local=args.sync_local,
    )
    if args.launch:
        codex_args = list(getattr(args, "codex_args", []))
        if codex_args and codex_args[0] == "--":
            codex_args = codex_args[1:]
        codex_args = _apply_profile_launch_defaults(profile, codex_args)
        return manager.launch(profile, extra_args=codex_args, local_port=args.local_port)
    _print_status_values(
        result,
        [
            "status",
            "install_action",
            "local_install_action",
            "auth_action",
            "runtime",
            "local_codex_version",
            "remote_codex_version",
            "remote_auth_status",
            "local_port",
            "remote_port",
            "workspace",
            "warning",
        ],
    )
    return 0


def _cmd_connect(args: argparse.Namespace) -> int:
    store = _profile_store()
    profile = store.get_profile(args.target)
    if profile is None:
        if not args.remote_root:
            raise SystemExit("remote-root is required when importing a new profile from an SSH alias")
        profile_name = args.name or args.target
        profile = _build_ssh_config_profile(
            alias=args.target,
            profile_name=profile_name,
            remote_root=args.remote_root,
            config_path=Path(args.ssh_config_path).expanduser(),
            codex_binary=args.codex_binary,
            codex_version=args.codex_version,
            codex_app_server_port=args.codex_app_server_port,
            service_scope=args.service_scope,
            default_model=args.default_model,
            default_cd=args.default_cd,
            sync_local_on_up=args.sync_local_on_up,
            api_key_env=args.api_key_env,
            auto_login_on_up=args.auto_login_on_up,
        )
        store.save_profile(profile)
    if not args.no_default:
        store.set_default_profile(profile.name)

    manager = RemoteCodexManager()
    result = manager.up(
        profile,
        local_port=args.local_port,
        sync_local=args.sync_local,
    )
    if not args.no_launch:
        codex_args = list(getattr(args, "codex_args", []))
        if codex_args and codex_args[0] == "--":
            codex_args = codex_args[1:]
        codex_args = _apply_profile_launch_defaults(profile, codex_args)
        return manager.launch(profile, extra_args=codex_args, local_port=args.local_port)
    _emit_payload(
        result,
        [
            "status",
            "install_action",
            "local_install_action",
            "auth_action",
            "runtime",
            "local_codex_version",
            "remote_codex_version",
            "remote_auth_status",
            "local_port",
            "remote_port",
            "workspace",
            "warning",
        ],
        as_json=args.json,
    )
    return 1 if args.strict and result.get("warning") else 0


def _cmd_launch(args: argparse.Namespace) -> int:
    profile = _require_profile(args.profile_name)
    manager = RemoteCodexManager(local_codex_binary=args.local_codex_binary)
    codex_args = list(getattr(args, "codex_args", []))
    if codex_args and codex_args[0] == "--":
        codex_args = codex_args[1:]
    return manager.launch(profile, extra_args=codex_args, local_port=args.local_port)


def _apply_profile_launch_defaults(profile: SSHProfile, codex_args: list[str]) -> list[str]:
    effective_args = list(codex_args)
    if profile.default_cd and "-C" not in effective_args and "--cd" not in effective_args:
        effective_args = ["-C", profile.default_cd, *effective_args]
    if profile.default_model and "-m" not in effective_args and "--model" not in effective_args:
        effective_args = ["-m", profile.default_model, *effective_args]
    return effective_args


def _cmd_open(args: argparse.Namespace) -> int:
    profile = _require_profile(args.profile_name)
    manager = RemoteCodexManager()
    codex_args = list(getattr(args, "codex_args", []))
    if codex_args and codex_args[0] == "--":
        codex_args = codex_args[1:]
    codex_args = _apply_profile_launch_defaults(profile, codex_args)
    manager.up(
        profile,
        local_port=args.local_port,
        sync_local=profile.sync_local_on_up,
        auto_login=profile.auto_login_on_up,
        api_key_env=profile.api_key_env,
    )
    return manager.launch(profile, extra_args=codex_args, local_port=args.local_port)


def _print_status_values(payload: dict[str, object], keys: list[str]) -> None:
    for key in keys:
        value = payload.get(key, "")
        print(f"{key}={'' if value is None else value}")


def _emit_payload(payload: dict[str, object], keys: list[str], *, as_json: bool = False) -> None:
    if as_json:
        filtered = {key: payload.get(key) for key in keys}
        print(json.dumps(filtered, indent=2))
        return
    _print_status_values(payload, keys)


def _cmd_start(args: argparse.Namespace) -> int:
    profile = _require_profile(args.profile_name)
    status = RemoteCodexManager().ensure_remote_app_server(profile)
    _emit_payload(status, ["status", "pid", "port", "log_file", "workspace"], as_json=args.json)
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    profile = _require_profile(args.profile_name)
    status = RemoteCodexManager().status(profile)
    _emit_payload(status, ["status", "pid", "port", "log_file", "workspace"], as_json=args.json)
    return 0


def _cmd_stop(args: argparse.Namespace) -> int:
    profile = _require_profile(args.profile_name)
    status = RemoteCodexManager().stop(profile)
    _emit_payload(status, ["status", "pid", "port", "log_file", "workspace"], as_json=args.json)
    return 0


def _cmd_smoke(args: argparse.Namespace) -> int:
    profile = _require_profile(args.profile_name)
    result = RemoteCodexManager().smoke(profile, local_port=args.local_port)
    _emit_payload(
        result,
        ["status", "remote_status", "local_port", "remote_port", "workspace"],
        as_json=args.json,
    )
    return 0


def _cmd_up(args: argparse.Namespace) -> int:
    profile = _require_profile(args.profile_name)
    manager = RemoteCodexManager()
    result = manager.up(
        profile,
        local_port=args.local_port,
        sync_local=args.sync_local,
    )
    if args.launch:
        codex_args = list(getattr(args, "codex_args", []))
        if codex_args and codex_args[0] == "--":
            codex_args = codex_args[1:]
        codex_args = _apply_profile_launch_defaults(profile, codex_args)
        return manager.launch(profile, extra_args=codex_args, local_port=args.local_port)
    _emit_payload(
        result,
        [
            "status",
            "install_action",
            "local_install_action",
            "auth_action",
            "runtime",
            "local_codex_version",
            "remote_codex_version",
            "remote_auth_status",
            "local_port",
            "remote_port",
            "workspace",
            "warning",
        ],
        as_json=args.json,
    )
    return 1 if args.strict and result.get("warning") else 0


def _cmd_bootstrap(args: argparse.Namespace) -> int:
    profile = _require_profile(args.profile_name)
    result = RemoteCodexManager().bootstrap(profile)
    _emit_payload(result, ["status", "helper_path", "helper_version"], as_json=args.json)
    return 0


def _cmd_install_codex(args: argparse.Namespace) -> int:
    profile = _require_profile(args.profile_name)
    result = RemoteCodexManager().install_remote_codex(profile, version=args.version)
    _emit_payload(
        result,
        ["status", "package_spec", "codex_path", "codex_version", "restart_status"],
        as_json=args.json,
    )
    return 0


def _cmd_auth_login(args: argparse.Namespace) -> int:
    profile = _require_profile(args.profile_name)
    result = RemoteCodexManager().login_remote_codex(
        profile,
        api_key_env=args.api_key_env or profile.api_key_env,
    )
    _emit_payload(
        result,
        ["status", "codex_path", "codex_version", "auth_status", "auth_message"],
        as_json=args.json,
    )
    return 0


def _cmd_upgrade_codex(args: argparse.Namespace) -> int:
    profile = _require_profile(args.profile_name)
    result = RemoteCodexManager().upgrade_remote_codex(profile)
    _emit_payload(
        result,
        ["status", "codex_path", "codex_version", "restart_status"],
        as_json=args.json,
    )
    return 0


def _cmd_service_install(args: argparse.Namespace) -> int:
    profile = _require_profile(args.profile_name)
    result = RemoteCodexManager().service_install(profile)
    _emit_payload(
        result,
        ["status", "enabled", "service_name", "service_scope", "unit_path"],
        as_json=args.json,
    )
    return 0


def _cmd_service_status(args: argparse.Namespace) -> int:
    profile = _require_profile(args.profile_name)
    result = RemoteCodexManager().service_status(profile)
    _emit_payload(
        result,
        ["status", "enabled", "service_name", "service_scope", "unit_path"],
        as_json=args.json,
    )
    return 0


def _cmd_service_start(args: argparse.Namespace) -> int:
    profile = _require_profile(args.profile_name)
    result = RemoteCodexManager().service_start(profile)
    _emit_payload(
        result,
        ["status", "enabled", "service_name", "service_scope", "unit_path"],
        as_json=args.json,
    )
    return 0


def _cmd_service_stop(args: argparse.Namespace) -> int:
    profile = _require_profile(args.profile_name)
    result = RemoteCodexManager().service_stop(profile)
    _emit_payload(
        result,
        ["status", "enabled", "service_name", "service_scope", "unit_path"],
        as_json=args.json,
    )
    return 0


def _cmd_service_uninstall(args: argparse.Namespace) -> int:
    profile = _require_profile(args.profile_name)
    result = RemoteCodexManager().service_uninstall(profile)
    _emit_payload(
        result,
        ["status", "enabled", "service_name", "service_scope", "unit_path"],
        as_json=args.json,
    )
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    profile = _require_profile(args.profile_name)
    result = RemoteCodexManager().doctor(profile, local_port=args.local_port)
    _emit_payload(
        result,
        [
            "status",
            "local_codex_version",
            "remote_codex_path",
            "remote_codex_version",
            "remote_auth_status",
            "remote_auth_message",
            "local_port",
            "remote_port",
            "workspace",
            "warning",
        ],
        as_json=args.json,
    )
    return 1 if args.strict and result.get("warning") else 0


def _cmd_logs(args: argparse.Namespace) -> int:
    profile = _require_profile(args.profile_name)
    output = RemoteCodexManager().read_remote_logs(profile, lines=args.lines)
    if args.json:
        print(json.dumps({"output": output}, indent=2))
        return 0
    if output:
        print(output, end="" if output.endswith("\n") else "\n")
    return 0


def _cmd_completion(args: argparse.Namespace) -> int:
    print(_render_completion_script(args.shell), end="")
    return 0


def _cmd_hidden_complete(args: argparse.Namespace) -> int:
    tokens = list(args.tokens)
    if tokens and tokens[0] == "--":
        tokens = tokens[1:]
    for candidate in _complete_tokens(tokens, args.current):
        print(candidate)
    return 0


def _cmd_install_cli(args: argparse.Namespace) -> int:
    bin_dir = Path(args.bin_dir).expanduser()
    bin_dir.mkdir(parents=True, exist_ok=True)
    launcher_path = bin_dir / "codex-remote"
    source = shutil.which("codex-remote")
    if source:
        source_path = Path(source).resolve()
    else:
        source_path = (Path(__file__).resolve().parents[2] / ".venv" / "bin" / "codex-remote")
    if not source_path.exists():
        raise SystemExit(f"codex-remote launcher not found: {source_path}")
    if launcher_path.exists() or launcher_path.is_symlink():
        launcher_path.unlink()
    launcher_path.symlink_to(source_path)
    payload = {
        "status": "installed",
        "launcher": str(launcher_path),
        "target": str(source_path),
    }
    if args.shell_completion:
        completion_target = _completion_install_target(args.shell_completion)
        completion_target.parent.mkdir(parents=True, exist_ok=True)
        completion_target.write_text(_render_completion_script(args.shell_completion))
        payload.update(
            {
                "completion_shell": args.shell_completion,
                "completion_path": str(completion_target),
                "completion_hint": _completion_activation_hint(args.shell_completion, completion_target),
            }
        )
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    _print_status_values(
        payload,
        ["status", "launcher", "target", "completion_shell", "completion_path", "completion_hint"],
    )
    return 0


def _bundle_root_name(profile_name: str, generated_at_utc: str) -> str:
    timestamp = (
        generated_at_utc.replace("-", "")
        .replace(":", "")
        .replace("T", "-")
        .replace("Z", "Z")
    )
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", profile_name).strip("-") or "default"
    return f"codex-remote-support-{safe_name}-{timestamp}"


def _capture_support_step(callback: Callable[[], object]) -> dict[str, object]:
    try:
        return {
            "status": "ok",
            "payload": callback(),
        }
    except Exception as exc:
        return {
            "status": "failed",
            "error": str(exc),
        }


def _redact_support_value(value: object) -> object:
    if isinstance(value, str):
        redacted = value
        for pattern, replacement in SUPPORT_BUNDLE_REDACTIONS:
            redacted = pattern.sub(replacement, redacted)
        return redacted
    if isinstance(value, dict):
        return {str(key): _redact_support_value(nested) for key, nested in value.items()}
    if isinstance(value, list):
        return [_redact_support_value(item) for item in value]
    return value


def _write_bundle_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n")


def _cmd_support_bundle(args: argparse.Namespace) -> int:
    profile = _require_profile(args.profile_name)
    store = _profile_store()
    manager = RemoteCodexManager()
    generated_at_utc = _utc_now_iso()
    bundle_root_name = _bundle_root_name(profile.name, generated_at_utc)
    output_path = (
        Path(args.output).expanduser()
        if args.output
        else Path.cwd() / f"{bundle_root_name}.tar.gz"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    status_step = _capture_support_step(lambda: manager.status(profile))
    doctor_step = _capture_support_step(lambda: manager.doctor(profile, local_port=args.local_port))
    logs_step = _capture_support_step(lambda: manager.read_remote_logs(profile, lines=args.lines))
    redacted = not args.no_redact
    profile_store_warnings: object = store.get_load_warnings()
    if redacted:
        status_step = _redact_support_value(status_step)
        doctor_step = _redact_support_value(doctor_step)
        logs_step = _redact_support_value(logs_step)
        profile_store_warnings = _redact_support_value(profile_store_warnings)

    manifest = {
        "status": "ok"
        if all(step["status"] == "ok" for step in (status_step, doctor_step, logs_step))
        and not profile_store_warnings
        else "warning",
        "generated_at_utc": generated_at_utc,
        "profile_name": profile.name,
        "bundle_version": 1,
        "redacted": redacted,
        "cwd": str(Path.cwd()),
        "platform": sys.platform,
        "python_version": platform.python_version(),
        "steps": {
            "status": status_step["status"],
            "doctor": doctor_step["status"],
            "logs": logs_step["status"],
            "profile_store": "warning" if profile_store_warnings else "ok",
        },
    }

    included_files: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / bundle_root_name
        root.mkdir(parents=True, exist_ok=True)

        manifest_path = root / "manifest.json"
        _write_bundle_json(manifest_path, manifest)
        included_files.append(str(Path(bundle_root_name) / manifest_path.name))

        profile_path = root / "profile.json"
        _write_bundle_json(
            profile_path,
            {
                **_profile_payload(profile),
                "is_default": profile.name == store.get_default_profile_name(),
            },
        )
        included_files.append(str(Path(bundle_root_name) / profile_path.name))

        profile_store_path = root / "profile-store.json"
        _write_bundle_json(
            profile_store_path,
            {
                "path": str(store.path),
                "default_profile": store.get_default_profile_name(),
                "profile_names": [saved.name for saved in store.list_profiles()],
                "warnings": profile_store_warnings,
            },
        )
        included_files.append(str(Path(bundle_root_name) / profile_store_path.name))

        status_path = root / "status.json"
        _write_bundle_json(status_path, status_step)
        included_files.append(str(Path(bundle_root_name) / status_path.name))

        doctor_path = root / "doctor.json"
        _write_bundle_json(doctor_path, doctor_step)
        included_files.append(str(Path(bundle_root_name) / doctor_path.name))

        if logs_step["status"] == "ok":
            logs_path = root / "logs.txt"
            logs_path.write_text(str(logs_step["payload"]))
        else:
            logs_path = root / "logs.error.txt"
            logs_path.write_text(str(logs_step["error"]) + "\n")
        included_files.append(str(Path(bundle_root_name) / logs_path.name))

        with tarfile.open(output_path, "w:gz") as archive:
            archive.add(root, arcname=bundle_root_name)

    payload = {
        "status": manifest["status"],
        "bundle": str(output_path),
        "profile_name": profile.name,
        "generated_at_utc": generated_at_utc,
        "included_files": included_files,
    }
    _emit_payload(
        payload,
        ["status", "bundle", "profile_name", "generated_at_utc", "included_files"],
        as_json=args.json,
    )
    if not args.json:
        for path in included_files:
            print(f"file={path}")
    return 0


def _run_packaging_step(
    command: list[str],
    *,
    cwd: Path,
    preserve_stdout: bool = False,
    preserve_stderr: bool = False,
) -> dict[str, object]:
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        text=True,
        capture_output=True,
    )
    payload: dict[str, object] = {
        "status": "ok" if completed.returncode == 0 else "failed",
        "command": command,
        "returncode": completed.returncode,
    }
    if preserve_stdout or completed.returncode != 0:
        payload["stdout"] = completed.stdout
    if preserve_stderr or completed.returncode != 0:
        payload["stderr"] = completed.stderr
    return payload


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _collect_release_context(project_root: Path) -> dict[str, object]:
    project_name = project_root.name
    project_version: str | None = None
    pyproject_path = project_root / "pyproject.toml"
    if pyproject_path.exists():
        pyproject = tomllib.loads(pyproject_path.read_text())
        project = pyproject.get("project", {})
        project_name = str(project.get("name", project_name))
        version = project.get("version")
        project_version = str(version) if version is not None else None

    plugin_name: str | None = None
    plugin_version: str | None = None
    plugin_manifest_path = project_root / ".codex-plugin" / "plugin.json"
    if plugin_manifest_path.exists():
        plugin_manifest = json.loads(plugin_manifest_path.read_text())
        plugin_name = plugin_manifest.get("name")
        plugin_version = plugin_manifest.get("version")

    git_commit: str | None = None
    git_dirty: bool | None = None
    if (project_root / ".git").exists():
        commit_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(project_root),
            text=True,
            capture_output=True,
        )
        if commit_result.returncode == 0:
            git_commit = commit_result.stdout.strip() or None
        dirty_result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(project_root),
            text=True,
            capture_output=True,
        )
        if dirty_result.returncode == 0:
            git_dirty = bool(dirty_result.stdout.strip())

    return {
        "generated_at_utc": _utc_now_iso(),
        "project": {
            "name": project_name,
            "version": project_version,
        },
        "plugin": {
            "name": plugin_name,
            "version": plugin_version,
        },
        "git": {
            "commit": git_commit,
            "dirty": git_dirty,
        },
        "builder": {
            "python_version": platform.python_version(),
            "python_executable": sys.executable,
            "platform": sys.platform,
        },
    }


def _evaluate_release_validation(
    context: dict[str, object],
    *,
    expected_version: str | None = None,
    artifacts_present: bool | None = True,
) -> dict[str, object]:
    project = context.get("project", {})
    plugin = context.get("plugin", {})
    git = context.get("git", {})
    project_version = project.get("version") if isinstance(project, dict) else None
    plugin_version = plugin.get("version") if isinstance(plugin, dict) else None
    git_dirty = git.get("dirty") if isinstance(git, dict) else None

    resolved_expected_version = expected_version
    if not resolved_expected_version:
        github_ref_name = os.environ.get("GITHUB_REF_NAME", "")
        if github_ref_name.startswith("v") and len(github_ref_name) > 1:
            resolved_expected_version = github_ref_name[1:]

    plugin_version_matches_project = (
        project_version is not None
        and plugin_version is not None
        and project_version == plugin_version
    )
    expected_version_matches_project = (
        resolved_expected_version is None
        or (project_version is not None and resolved_expected_version == project_version)
    )
    git_clean = None if git_dirty is None else not bool(git_dirty)

    warnings: list[str] = []
    if project_version is not None and plugin_version is not None and not plugin_version_matches_project:
        warnings.append("plugin-package-version-mismatch")
    if resolved_expected_version is not None and not expected_version_matches_project:
        warnings.append("expected-version-mismatch")
    if git_clean is False:
        warnings.append("git-dirty")
    if artifacts_present is False:
        warnings.append("release-artifacts-missing")

    return {
        "project_version": project_version,
        "plugin_version": plugin_version,
        "expected_version": resolved_expected_version,
        "plugin_version_matches_project": plugin_version_matches_project,
        "expected_version_matches_project": expected_version_matches_project,
        "git_clean": git_clean,
        "artifacts_present": artifacts_present,
        "warnings": warnings,
    }


def _write_release_metadata(
    dist_dir: Path,
    artifacts: list[Path],
    *,
    context: dict[str, object] | None = None,
) -> dict[str, str]:
    normalized_artifacts = [path.resolve() for path in artifacts]
    checksum_lines: list[str] = []
    manifest_artifacts: list[dict[str, object]] = []

    for artifact in normalized_artifacts:
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        checksum_lines.append(f"{digest}  {artifact.name}")
        manifest_artifacts.append(
            {
                "name": artifact.name,
                "path": str(artifact),
                "size": artifact.stat().st_size,
                "sha256": digest,
            }
        )

    checksums_path = dist_dir / "SHA256SUMS"
    checksums_path.write_text("".join(f"{line}\n" for line in checksum_lines))

    manifest_path = dist_dir / "release-manifest.json"
    manifest_payload: dict[str, object] = {
        "status": "ok",
        "artifacts": manifest_artifacts,
    }
    if context:
        manifest_payload.update(context)
    manifest_path.write_text(
        json.dumps(manifest_payload, indent=2)
        + "\n"
    )

    return {
        "checksums": str(checksums_path),
        "manifest": str(manifest_path),
    }


def _clean_release_outputs(dist_dir: Path) -> list[str]:
    removed: list[str] = []
    for pattern in MANAGED_RELEASE_GLOBS:
        for path in dist_dir.glob(pattern):
            if not path.exists() or not path.is_file():
                continue
            path.unlink()
            removed.append(str(path))
    return sorted(set(removed))


def _emit_package_result(result: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, indent=2))
        return
    print(f"status={result['status']}")
    print(f"project_root={result['project_root']}")
    for step_name in ("tests", "build", "bundle"):
        step = result.get(step_name)
        if not isinstance(step, dict):
            continue
        print(f"{step_name}={step.get('status')}")
    validation = result.get("validation")
    if isinstance(validation, dict):
        warnings = validation.get("warnings", [])
        print(f"validation_warnings={','.join(warnings) if warnings else '-'}")
    for artifact in result.get("artifacts", []):
        print(f"artifact={artifact}")


def _cmd_package(args: argparse.Namespace) -> int:
    root = _project_root()
    if not (root / "pyproject.toml").exists() or not (root / "scripts" / "build_plugin_bundle.py").exists():
        raise SystemExit("package command requires a source checkout with pyproject.toml and scripts/build_plugin_bundle.py")

    result: dict[str, object] = {
        "status": "ok",
        "project_root": str(root),
    }
    release_context = _collect_release_context(root)
    result.update(release_context)
    preflight_validation = _evaluate_release_validation(
        release_context,
        expected_version=args.expected_version,
        artifacts_present=None,
    )
    if args.strict_release and preflight_validation["warnings"]:
        result["status"] = "failed"
        result["validation"] = preflight_validation
        _emit_package_result(result, as_json=args.json)
        return 1

    dist_dir = root / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)
    if not args.no_clean:
        result["cleaned"] = _clean_release_outputs(dist_dir)

    if not args.skip_tests:
        tests_result = _run_packaging_step(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
            cwd=root,
        )
        result["tests"] = tests_result
        if tests_result["status"] != "ok":
            result["status"] = "failed"
            _emit_package_result(result, as_json=args.json)
            return int(tests_result["returncode"]) or 1

    if not args.skip_build:
        build_result = _run_packaging_step([sys.executable, "-m", "build"], cwd=root)
        result["build"] = build_result
        if build_result["status"] != "ok":
            result["status"] = "failed"
            _emit_package_result(result, as_json=args.json)
            return int(build_result["returncode"]) or 1
    artifact_paths: list[Path] = []
    artifact_paths.extend(sorted(dist_dir.glob("ssh_remote_control-*.whl")))
    artifact_paths.extend(sorted(dist_dir.glob("ssh_remote_control-*.tar.gz")))

    if not args.skip_bundle:
        bundle_output = Path(args.bundle_output).expanduser() if args.bundle_output else dist_dir / "ssh-remote-control-plugin.zip"
        bundle_command = [
            sys.executable,
            str(root / "scripts" / "build_plugin_bundle.py"),
            "--json",
            "--output",
            str(bundle_output),
        ]
        bundle_result = _run_packaging_step(bundle_command, cwd=root, preserve_stdout=True)
        parsed_bundle: dict[str, object] | None = None
        if bundle_result["status"] == "ok":
            parsed_bundle = json.loads(str(bundle_result["stdout"]))
        result["bundle"] = parsed_bundle or bundle_result
        if bundle_result["status"] != "ok":
            result["status"] = "failed"
            _emit_package_result(result, as_json=args.json)
            return int(bundle_result["returncode"]) or 1
        if parsed_bundle is not None:
            artifact_paths.append(Path(str(parsed_bundle["bundle"])).resolve())

    validation = _evaluate_release_validation(
        release_context,
        expected_version=args.expected_version,
        artifacts_present=bool(artifact_paths),
    )
    result["validation"] = validation
    if validation["warnings"] and (args.strict_release or "release-artifacts-missing" in validation["warnings"]):
        result["status"] = "failed"
        _emit_package_result(result, as_json=args.json)
        return 1

    metadata = _write_release_metadata(
        dist_dir,
        artifact_paths,
        context={**release_context, "validation": validation},
    )
    result["checksums"] = metadata["checksums"]
    result["manifest"] = metadata["manifest"]
    result["artifacts"] = [str(path) for path in artifact_paths] + [
        metadata["checksums"],
        metadata["manifest"],
    ]
    _emit_package_result(result, as_json=args.json)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args, extra = parser.parse_known_args(argv)
    if args.command in {"launch", "open"}:
        args.codex_args = extra
    elif args.command == "connect":
        args.codex_args = extra
        if extra and args.no_launch:
            parser.error(f"unrecognized arguments: {' '.join(extra)}")
    elif args.command in {"init", "up"}:
        args.codex_args = extra
        if extra and not args.launch:
            parser.error(f"unrecognized arguments: {' '.join(extra)}")
    elif extra:
        parser.error(f"unrecognized arguments: {' '.join(extra)}")

    if args.command == "profile":
        if args.profile_command == "save":
            return _cmd_profile_save(args)
        if args.profile_command == "import-ssh-config":
            return _cmd_profile_import_ssh_config(args)
        if args.profile_command == "list":
            return _cmd_profile_list(as_json=args.json)
        if args.profile_command == "current":
            return _cmd_profile_current(as_json=args.json)
        if args.profile_command == "aliases":
            return _cmd_profile_aliases(args)
        if args.profile_command == "show":
            return _cmd_profile_show(args)
        if args.profile_command == "doctor":
            return _cmd_profile_doctor(args)
        if args.profile_command == "delete":
            return _cmd_profile_delete(args)
        if args.profile_command == "use":
            return _cmd_profile_use(args)
    if args.command == "init":
        return _cmd_init(args)
    if args.command == "connect":
        return _cmd_connect(args)

    if args.command == "launch":
        return _cmd_launch(args)
    if args.command == "open":
        return _cmd_open(args)
    if args.command == "start":
        return _cmd_start(args)
    if args.command == "status":
        return _cmd_status(args)
    if args.command == "stop":
        return _cmd_stop(args)
    if args.command == "smoke":
        return _cmd_smoke(args)
    if args.command == "up":
        return _cmd_up(args)
    if args.command == "bootstrap":
        return _cmd_bootstrap(args)
    if args.command == "install-codex":
        return _cmd_install_codex(args)
    if args.command == "auth-login":
        return _cmd_auth_login(args)
    if args.command == "upgrade-codex":
        return _cmd_upgrade_codex(args)
    if args.command == "service-install":
        return _cmd_service_install(args)
    if args.command == "service-status":
        return _cmd_service_status(args)
    if args.command == "service-start":
        return _cmd_service_start(args)
    if args.command == "service-stop":
        return _cmd_service_stop(args)
    if args.command == "service-uninstall":
        return _cmd_service_uninstall(args)
    if args.command == "doctor":
        return _cmd_doctor(args)
    if args.command == "logs":
        return _cmd_logs(args)
    if args.command == "install-cli":
        return _cmd_install_cli(args)
    if args.command == "completion":
        return _cmd_completion(args)
    if args.command == "support-bundle":
        return _cmd_support_bundle(args)
    if args.command == "_complete":
        return _cmd_hidden_complete(args)
    if args.command == "package":
        return _cmd_package(args)

    parser.error(f"unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

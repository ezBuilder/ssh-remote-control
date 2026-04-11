from __future__ import annotations

import os
import posixpath
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

try:
    import paramiko  # type: ignore
except ImportError:  # pragma: no cover - handled at runtime
    paramiko = None

from mcp.server.fastmcp import FastMCP

from ssh_remote_control.credential_store import CredentialStore
from ssh_remote_control.models import AuthMode
from ssh_remote_control.models import PasswordStorage
from ssh_remote_control.models import SSHProfile
from ssh_remote_control.profile_store import ProfileStore
from ssh_remote_control.session_manager import SessionManager


PLUGIN_ROOT = Path(__file__).resolve().parents[2]


def codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".codex"


STATE_ROOT = codex_home() / "ssh-remote-control"
PROFILES_PATH = STATE_ROOT / "profiles.json"

app = FastMCP("ssh-remote-control")
profile_store = ProfileStore(PROFILES_PATH)
credential_store = CredentialStore()
session_manager = SessionManager()


def _profile_to_summary(profile: SSHProfile) -> dict[str, Any]:
    return {
        "name": profile.name,
        "host": profile.host,
        "port": profile.port,
        "username": profile.username,
        "remote_root": profile.remote_root,
        "codex_binary": profile.codex_binary,
        "codex_app_server_port": profile.codex_app_server_port,
        "auth_mode": profile.auth_mode.value,
        "password_storage": profile.password_storage.value,
        "ssh_config_host": profile.ssh_config_host,
        "key_path_configured": bool(profile.key_path),
        "allow_connect_without_confirmation": profile.allow_connect_without_confirmation,
        "allowed_exec_prefixes": profile.allowed_exec_prefixes,
        "allowed_read_roots": profile.allowed_read_roots,
        "allowed_write_roots": profile.allowed_write_roots,
    }


def _resolve_profile(profile: SSHProfile) -> SSHProfile:
    if paramiko is None:
        return profile

    alias = profile.ssh_config_host
    if alias is None and profile.auth_mode == AuthMode.SSH_CONFIG:
        alias = profile.name
    if alias is None:
        return profile

    config_path = Path.home() / ".ssh" / "config"
    if not config_path.exists():
        return profile

    ssh_config = paramiko.SSHConfig()
    with config_path.open() as handle:
        ssh_config.parse(handle)
    resolved = ssh_config.lookup(alias)
    identity_files = resolved.get("identityfile") or []
    key_path = profile.key_path or (identity_files[0] if identity_files else None)
    auth_mode = profile.auth_mode
    if key_path and auth_mode == AuthMode.SSH_CONFIG:
        auth_mode = AuthMode.KEY_FILE

    return SSHProfile(
        name=profile.name,
        host=resolved.get("hostname", profile.host or alias),
        port=int(resolved.get("port", profile.port)),
        username=profile.username or resolved.get("user"),
        remote_root=profile.remote_root,
        codex_binary=profile.codex_binary,
        codex_app_server_port=profile.codex_app_server_port,
        auth_mode=auth_mode,
        password_storage=profile.password_storage,
        ssh_config_host=profile.ssh_config_host,
        key_path=str(Path(key_path).expanduser()) if key_path else None,
    )


def _require_profile(profile_name: str) -> SSHProfile:
    profile = profile_store.get_profile(profile_name)
    if profile is None:
        raise ValueError(f"unknown SSH profile: {profile_name}")
    return profile


def _resolve_policy_path(profile: SSHProfile, path: str) -> PurePosixPath:
    resolved = SessionManager._resolve_remote_path(profile, path)
    normalized = posixpath.normpath(resolved or ".")
    return PurePosixPath(normalized)


def _path_is_allowlisted(
    profile: SSHProfile,
    requested_path: str,
    allowed_roots: list[str],
) -> bool:
    if not allowed_roots:
        return False

    target = _resolve_policy_path(profile, requested_path)
    for allowed_root in allowed_roots:
        allowed = _resolve_policy_path(profile, allowed_root)
        if target == allowed or allowed in target.parents:
            return True
    return False


def _command_is_allowlisted(command: str, allowed_prefixes: list[str]) -> bool:
    normalized_command = command.strip()
    for prefix in allowed_prefixes:
        normalized_prefix = prefix.strip()
        if normalized_prefix and normalized_command.startswith(normalized_prefix):
            return True
    return False


def _require_connect_authorization(profile: SSHProfile, confirm: bool) -> None:
    if confirm or profile.allow_connect_without_confirmation:
        return
    raise ValueError(
        "ssh_connect requires confirm=True or allow_connect_without_confirmation on the profile"
    )


def _require_exec_authorization(
    profile: SSHProfile,
    command: str,
    confirm: bool,
) -> None:
    if confirm or _command_is_allowlisted(command, profile.allowed_exec_prefixes):
        return
    raise ValueError(
        "ssh_exec requires confirm=True unless the command matches allowed_exec_prefixes"
    )


def _require_read_authorization(
    profile: SSHProfile,
    remote_path: str,
    confirm: bool,
) -> None:
    if confirm or _path_is_allowlisted(profile, remote_path, profile.allowed_read_roots):
        return
    raise ValueError(
        "Remote reads require confirm=True unless the path matches allowed_read_roots"
    )


def _require_write_authorization(
    profile: SSHProfile,
    remote_path: str,
    confirm: bool,
) -> None:
    if confirm or _path_is_allowlisted(profile, remote_path, profile.allowed_write_roots):
        return
    raise ValueError(
        "Remote writes require confirm=True unless the path matches allowed_write_roots"
    )


def _maybe_store_secret(
    profile: SSHProfile,
    *,
    password: str | None = None,
    passphrase: str | None = None,
) -> None:
    if password:
        credential_store.save_secret(
            profile.name, "password", password, profile.password_storage
        )
    if passphrase:
        credential_store.save_secret(
            profile.name, "passphrase", passphrase, profile.password_storage
        )


def _resolve_secret(
    profile: SSHProfile,
    supplied_value: str | None,
    secret_kind: str,
) -> str | None:
    if supplied_value:
        return supplied_value
    return credential_store.load_secret(profile.name, secret_kind, profile.password_storage)


@app.tool()
def ssh_profile_save(
    name: str,
    host: str | None = None,
    port: int = 22,
    username: str | None = None,
    remote_root: str | None = None,
    codex_binary: str = "codex",
    codex_app_server_port: int = 4500,
    auth_mode: str = AuthMode.SSH_CONFIG.value,
    password_storage: str = PasswordStorage.NEVER.value,
    ssh_config_host: str | None = None,
    key_path: str | None = None,
    allow_connect_without_confirmation: bool = False,
    allowed_exec_prefixes: list[str] | None = None,
    allowed_read_roots: list[str] | None = None,
    allowed_write_roots: list[str] | None = None,
) -> dict[str, Any]:
    """Save or update an SSH profile for later connections."""

    profile = SSHProfile(
        name=name,
        host=host or ssh_config_host or name,
        port=port,
        username=username,
        remote_root=remote_root,
        codex_binary=codex_binary,
        codex_app_server_port=codex_app_server_port,
        auth_mode=AuthMode(auth_mode),
        password_storage=PasswordStorage(password_storage),
        ssh_config_host=ssh_config_host,
        key_path=str(Path(key_path).expanduser()) if key_path else None,
        allow_connect_without_confirmation=allow_connect_without_confirmation,
        allowed_exec_prefixes=list(allowed_exec_prefixes or []),
        allowed_read_roots=list(allowed_read_roots or []),
        allowed_write_roots=list(allowed_write_roots or []),
    )
    profile_store.save_profile(profile)
    return {"saved": True, "profile": _profile_to_summary(profile)}


@app.tool()
def ssh_profile_list() -> dict[str, Any]:
    """List saved SSH profiles and current connection state."""

    profiles = []
    for profile in profile_store.list_profiles():
        entry = _profile_to_summary(profile)
        entry["connected"] = session_manager.connection_status(profile.name).value
        profiles.append(entry)
    return {"profiles": profiles}


@app.tool()
def ssh_connect(
    profile_name: str,
    password: str | None = None,
    passphrase: str | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Connect a saved SSH profile and keep the session alive in the MCP server."""

    stored_profile = _require_profile(profile_name)
    profile = _resolve_profile(stored_profile)
    _require_connect_authorization(profile, confirm)
    resolved_password = _resolve_secret(profile, password, "password")
    resolved_passphrase = _resolve_secret(profile, passphrase, "passphrase")
    session_manager.connect_profile(
        profile,
        password=resolved_password,
        passphrase=resolved_passphrase,
    )
    _maybe_store_secret(profile, password=password, passphrase=passphrase)
    return {
        "connected": True,
        "profile": _profile_to_summary(profile),
        "status": session_manager.connection_status(profile.name).value,
    }


@app.tool()
def ssh_disconnect(profile_name: str) -> dict[str, Any]:
    """Disconnect a saved SSH profile."""

    session_manager.disconnect_profile(profile_name)
    return {
        "disconnected": True,
        "profile_name": profile_name,
        "status": session_manager.connection_status(profile_name).value,
    }


@app.tool()
def ssh_status(profile_name: str | None = None) -> dict[str, Any]:
    """Return connection state for one profile or all saved profiles."""

    if profile_name:
        profile = _require_profile(profile_name)
        return {
            "profile": _profile_to_summary(profile),
            "status": session_manager.connection_status(profile_name).value,
        }

    return {
        "profiles": [
            {
                **_profile_to_summary(profile),
                "status": session_manager.connection_status(profile.name).value,
            }
            for profile in profile_store.list_profiles()
        ]
    }


@app.tool()
def ssh_exec(
    profile_name: str,
    command: str,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Run a shell command on a connected profile."""

    profile = _require_profile(profile_name)
    _require_exec_authorization(profile, command, confirm)
    return session_manager.run_command(profile_name, command, cwd=cwd, env=env)


@app.tool()
def ssh_read_file(
    profile_name: str,
    remote_path: str,
    confirm: bool = False,
) -> dict[str, Any]:
    """Read a UTF-8 text file from a connected profile."""

    profile = _require_profile(profile_name)
    _require_read_authorization(profile, remote_path, confirm)
    return {
        "path": remote_path,
        "content": session_manager.read_text_file(profile_name, remote_path),
    }


@app.tool()
def ssh_write_file(
    profile_name: str,
    remote_path: str,
    content: str,
    confirm: bool = False,
) -> dict[str, Any]:
    """Write a UTF-8 text file to a connected profile, replacing previous contents."""

    profile = _require_profile(profile_name)
    _require_write_authorization(profile, remote_path, confirm)
    session_manager.write_text_file(profile_name, remote_path, content)
    return {"written": True, "path": remote_path}


@app.tool()
def ssh_upload(
    profile_name: str,
    local_path: str,
    remote_path: str,
    confirm: bool = False,
) -> dict[str, Any]:
    """Upload a local file or directory to a connected profile."""

    profile = _require_profile(profile_name)
    _require_write_authorization(profile, remote_path, confirm)
    session_manager.upload_path(profile_name, local_path, remote_path)
    return {"uploaded": True, "local_path": local_path, "remote_path": remote_path}


@app.tool()
def ssh_download(
    profile_name: str,
    remote_path: str,
    local_path: str,
    confirm: bool = False,
) -> dict[str, Any]:
    """Download a remote file or directory from a connected profile."""

    profile = _require_profile(profile_name)
    _require_read_authorization(profile, remote_path, confirm)
    session_manager.download_path(profile_name, remote_path, local_path)
    return {"downloaded": True, "remote_path": remote_path, "local_path": local_path}


@app.tool()
def ssh_sync(
    profile_name: str,
    local_path: str,
    remote_path: str,
    direction: str = "upload",
    confirm: bool = False,
) -> dict[str, Any]:
    """Recursively sync content between local and remote paths."""

    profile = _require_profile(profile_name)
    if direction == "upload":
        _require_write_authorization(profile, remote_path, confirm)
    elif direction == "download":
        _require_read_authorization(profile, remote_path, confirm)
    else:
        raise ValueError(f"unsupported sync direction: {direction}")

    session_manager.sync_path(
        profile_name,
        local_path,
        remote_path,
        direction=direction,
    )
    return {
        "synced": True,
        "direction": direction,
        "local_path": local_path,
        "remote_path": remote_path,
    }


def main() -> None:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    app.run()


if __name__ == "__main__":
    main()

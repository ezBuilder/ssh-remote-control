from __future__ import annotations

import os
from pathlib import Path
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
        "auth_mode": profile.auth_mode.value,
        "password_storage": profile.password_storage.value,
        "ssh_config_host": profile.ssh_config_host,
        "key_path": profile.key_path,
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
    auth_mode: str = AuthMode.SSH_CONFIG.value,
    password_storage: str = PasswordStorage.NEVER.value,
    ssh_config_host: str | None = None,
    key_path: str | None = None,
) -> dict[str, Any]:
    """Save or update an SSH profile for later connections."""

    profile = SSHProfile(
        name=name,
        host=host or ssh_config_host or name,
        port=port,
        username=username,
        remote_root=remote_root,
        auth_mode=AuthMode(auth_mode),
        password_storage=PasswordStorage(password_storage),
        ssh_config_host=ssh_config_host,
        key_path=str(Path(key_path).expanduser()) if key_path else None,
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
) -> dict[str, Any]:
    """Connect a saved SSH profile and keep the session alive in the MCP server."""

    stored_profile = _require_profile(profile_name)
    profile = _resolve_profile(stored_profile)
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
) -> dict[str, Any]:
    """Run a shell command on a connected profile."""

    return session_manager.run_command(profile_name, command, cwd=cwd, env=env)


@app.tool()
def ssh_read_file(profile_name: str, remote_path: str) -> dict[str, Any]:
    """Read a UTF-8 text file from a connected profile."""

    return {
        "path": remote_path,
        "content": session_manager.read_text_file(profile_name, remote_path),
    }


@app.tool()
def ssh_write_file(profile_name: str, remote_path: str, content: str) -> dict[str, Any]:
    """Write a UTF-8 text file to a connected profile, replacing previous contents."""

    session_manager.write_text_file(profile_name, remote_path, content)
    return {"written": True, "path": remote_path}


@app.tool()
def ssh_upload(profile_name: str, local_path: str, remote_path: str) -> dict[str, Any]:
    """Upload a local file or directory to a connected profile."""

    session_manager.upload_path(profile_name, local_path, remote_path)
    return {"uploaded": True, "local_path": local_path, "remote_path": remote_path}


@app.tool()
def ssh_download(profile_name: str, remote_path: str, local_path: str) -> dict[str, Any]:
    """Download a remote file or directory from a connected profile."""

    session_manager.download_path(profile_name, remote_path, local_path)
    return {"downloaded": True, "remote_path": remote_path, "local_path": local_path}


@app.tool()
def ssh_sync(
    profile_name: str,
    local_path: str,
    remote_path: str,
    direction: str = "upload",
) -> dict[str, Any]:
    """Recursively sync content between local and remote paths."""

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

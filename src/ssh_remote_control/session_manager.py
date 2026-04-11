from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import posixpath
import shlex
import stat
from typing import Any, Callable

from ssh_remote_control.models import AuthMode
from ssh_remote_control.models import ConnectionStatus
from ssh_remote_control.models import SSHProfile


def _default_client_factory() -> Any:
    try:
        import paramiko  # type: ignore
    except ImportError as exc:  # pragma: no cover - exercised in integration usage
        raise RuntimeError(
            "paramiko is required to use the SSH remote control plugin"
        ) from exc

    return paramiko.SSHClient()


@dataclass(slots=True)
class Session:
    profile: SSHProfile
    client: Any
    sftp: Any


class SessionManager:
    def __init__(self, client_factory: Callable[[], Any] | None = None) -> None:
        self._client_factory = client_factory or _default_client_factory
        self._sessions: dict[str, Session] = {}

    def connect_profile(
        self,
        profile: SSHProfile,
        *,
        password: str | None = None,
        passphrase: str | None = None,
    ) -> None:
        if profile.name in self._sessions:
            self.disconnect_profile(profile.name)

        client = self._client_factory()
        if hasattr(client, "load_system_host_keys"):
            client.load_system_host_keys()
        connect_kwargs: dict[str, Any] = {
            "hostname": profile.host,
            "port": profile.port,
            "username": profile.username,
        }

        if profile.auth_mode == AuthMode.PASSWORD and password is not None:
            connect_kwargs["password"] = password
        if profile.auth_mode == AuthMode.KEY_FILE and profile.key_path is not None:
            connect_kwargs["key_filename"] = profile.key_path
            if passphrase is not None:
                connect_kwargs["passphrase"] = passphrase

        client.connect(**connect_kwargs)
        sftp = client.open_sftp()
        self._sessions[profile.name] = Session(profile=profile, client=client, sftp=sftp)

    def disconnect_profile(self, profile_name: str) -> None:
        session = self._sessions.pop(profile_name, None)
        if session is None:
            return
        session.sftp.close()
        session.client.close()

    def connection_status(self, profile_name: str) -> ConnectionStatus:
        if profile_name in self._sessions:
            return ConnectionStatus.CONNECTED
        return ConnectionStatus.DISCONNECTED

    def write_text_file(self, profile_name: str, path: str, content: str) -> None:
        session = self._require_session(profile_name)
        target_path = self._resolve_remote_path(session.profile, path)
        self._ensure_remote_directory(session.sftp, posixpath.dirname(target_path) or "/")
        with session.sftp.file(target_path, "wb") as handle:
            handle.write(content.encode("utf-8"))

    def read_text_file(self, profile_name: str, path: str) -> str:
        session = self._require_session(profile_name)
        target_path = self._resolve_remote_path(session.profile, path)
        with session.sftp.file(target_path, "rb") as handle:
            return handle.read().decode("utf-8")

    def run_command(
        self,
        profile_name: str,
        command: str,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        session = self._require_session(profile_name)
        target_cwd = self._resolve_remote_path(session.profile, cwd or session.profile.remote_root or "")
        remote_command = command
        if target_cwd:
            remote_command = f"cd {shlex.quote(target_cwd)} && {command}"
        _, stdout, stderr = session.client.exec_command(remote_command, environment=env)
        exit_status = stdout.channel.recv_exit_status()
        return {
            "command": remote_command,
            "cwd": target_cwd or None,
            "stdout": stdout.read().decode("utf-8"),
            "stderr": stderr.read().decode("utf-8"),
            "exit_status": exit_status,
        }

    def upload_path(self, profile_name: str, local_path: str | Path, remote_path: str) -> None:
        session = self._require_session(profile_name)
        source = Path(local_path)
        target = self._resolve_remote_path(session.profile, remote_path)
        if source.is_dir():
            self._ensure_remote_directory(session.sftp, target)
            for child in source.iterdir():
                child_target = posixpath.join(target, child.name)
                self.upload_path(profile_name, child, child_target)
            return

        self._ensure_remote_directory(session.sftp, posixpath.dirname(target) or "/")
        session.sftp.put(str(source), target)

    def download_path(self, profile_name: str, remote_path: str, local_path: str | Path) -> None:
        session = self._require_session(profile_name)
        source = self._resolve_remote_path(session.profile, remote_path)
        target = Path(local_path)
        if self._is_remote_directory(session.sftp, source):
            target.mkdir(parents=True, exist_ok=True)
            for entry in session.sftp.listdir_attr(source):
                child_remote = posixpath.join(source, entry.filename)
                child_local = target / entry.filename
                if stat.S_ISDIR(entry.st_mode):
                    self.download_path(profile_name, child_remote, child_local)
                else:
                    session.sftp.get(child_remote, str(child_local))
            return

        target.parent.mkdir(parents=True, exist_ok=True)
        session.sftp.get(source, str(target))

    def sync_path(
        self,
        profile_name: str,
        local_path: str | Path,
        remote_path: str,
        *,
        direction: str = "upload",
    ) -> None:
        if direction == "upload":
            self.upload_path(profile_name, local_path, remote_path)
            return
        if direction == "download":
            self.download_path(profile_name, remote_path, local_path)
            return
        raise ValueError(f"unsupported sync direction: {direction}")

    def _require_session(self, profile_name: str) -> Session:
        try:
            return self._sessions[profile_name]
        except KeyError as exc:
            raise KeyError(f"profile '{profile_name}' is not connected") from exc

    @staticmethod
    def _ensure_remote_directory(sftp: Any, remote_path: str) -> None:
        if remote_path in {"", "/"}:
            return
        parts = [part for part in remote_path.split("/") if part]
        current = ""
        for part in parts:
            current = f"{current}/{part}" if current else f"/{part}"
            try:
                sftp.stat(current)
            except FileNotFoundError:
                sftp.mkdir(current)

    @staticmethod
    def _is_remote_directory(sftp: Any, remote_path: str) -> bool:
        try:
            metadata = sftp.stat(remote_path)
        except FileNotFoundError:
            return False
        return stat.S_ISDIR(metadata.st_mode)

    @staticmethod
    def _resolve_remote_path(profile: SSHProfile, path: str) -> str:
        if not path:
            return profile.remote_root or ""
        if path.startswith("/"):
            return path
        if profile.remote_root:
            return posixpath.join(profile.remote_root, path)
        return path

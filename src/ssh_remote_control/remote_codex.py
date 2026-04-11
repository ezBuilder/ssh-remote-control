from __future__ import annotations

import base64
from contextlib import contextmanager
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import subprocess
import time
from typing import Any, Callable, Iterator, Protocol

from ssh_remote_control.models import SSHProfile

REMOTE_HELPER_BASENAME = "codex-remote-app-server"
REMOTE_HELPER_VERSION = 1


class ProcessLike(Protocol):
    pid: int

    def poll(self) -> int | None:
        ...

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        ...

    def terminate(self) -> None:
        ...

    def wait(self, timeout: float | None = None) -> int:
        ...

    def kill(self) -> None:
        ...


class RunnerLike(Protocol):
    def run(self, args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        ...

    def popen(self, args: list[str], **kwargs: object) -> ProcessLike:
        ...


class SubprocessRunner:
    def run(self, args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.run(args, **kwargs)

    def popen(self, args: list[str], **kwargs: object) -> ProcessLike:
        return subprocess.Popen(args, **kwargs)


def find_free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_websocket_ready(host: str, port: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5) as sock:
                sock.settimeout(0.5)
                key = base64.b64encode(os.urandom(16)).decode("ascii")
                request = (
                    f"GET / HTTP/1.1\r\n"
                    f"Host: {host}:{port}\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    f"Sec-WebSocket-Key: {key}\r\n"
                    "Sec-WebSocket-Version: 13\r\n"
                    "\r\n"
                )
                sock.sendall(request.encode("ascii"))

                response = b""
                while b"\r\n\r\n" not in response:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    response += chunk
                if response.startswith(b"HTTP/1.1 101") or response.startswith(
                    b"HTTP/1.0 101"
                ):
                    return
                last_error = RuntimeError(
                    f"unexpected websocket probe response from {host}:{port}: "
                    f"{response.splitlines()[:1]}"
                )
        except OSError as exc:
            last_error = exc
        time.sleep(0.1)
    raise TimeoutError(
        f"timed out waiting for websocket readiness on {host}:{port}: {last_error}"
    )


def _sanitize_profile_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-")
    return cleaned or "default"


def _parse_metadata(stdout: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    allowed_keys = {
        "status",
        "pid",
        "port",
        "log_file",
        "workspace",
        "runtime",
        "codex_path",
        "codex_version",
        "auth_status",
        "auth_message",
        "helper_path",
        "helper_version",
        "package_spec",
        "restart_status",
        "service_name",
        "service_scope",
        "unit_path",
        "enabled",
    }
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key not in allowed_keys:
            continue
        metadata[key] = value

    if "port" in metadata and metadata["port"]:
        metadata["port"] = int(metadata["port"])
    if "pid" in metadata:
        metadata["pid"] = int(metadata["pid"]) if metadata["pid"] else None
    return metadata


def _extract_codex_version_number(version_text: str | None) -> str | None:
    if not version_text:
        return None
    match = re.search(r"(\d+\.\d+\.\d+)", version_text)
    if match is None:
        return None
    return match.group(1)


class RemoteCodexManager:
    def __init__(
        self,
        *,
        runner: RunnerLike | None = None,
        ssh_binary: str = "ssh",
        local_codex_binary: str = "codex",
        wait_for_tunnel_ready: Callable[[str, int, float], None] = wait_for_websocket_ready,
        auto_bootstrap: bool = True,
    ) -> None:
        self.runner = runner or SubprocessRunner()
        self.ssh_binary = ssh_binary
        self.local_codex_binary = local_codex_binary
        self.wait_for_tunnel_ready = wait_for_tunnel_ready
        self.auto_bootstrap = auto_bootstrap
        self._bootstrapped_targets: set[str] = set()

    def bootstrap(self, profile: SSHProfile) -> dict[str, Any]:
        result = self.runner.run(
            self._build_remote_exec_command(profile, self._build_bootstrap_script()),
            text=True,
            capture_output=True,
        )
        metadata = _parse_metadata(result.stdout)
        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip() or "unknown remote error"
            raise RuntimeError(f"failed to bootstrap remote Codex helper: {details}")
        self._bootstrapped_targets.add(self._target_key(profile))
        return metadata

    def install_remote_codex(
        self,
        profile: SSHProfile,
        *,
        version: str | None = None,
    ) -> dict[str, Any]:
        self._ensure_remote_helper(profile)
        previous = self.status(profile)
        package_spec = "@openai/codex"
        requested_version = version or profile.codex_version
        if requested_version:
            package_spec = f"{package_spec}@{requested_version}"
        result = self.runner.run(
            self._build_remote_exec_command(
                profile,
                self._build_install_codex_script(profile, package_spec),
            ),
            text=True,
            capture_output=True,
        )
        metadata = _parse_metadata(result.stdout)
        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip() or "unknown remote error"
            raise RuntimeError(f"failed to install remote Codex runtime: {details}")
        metadata.update(self._restart_after_codex_update(profile, previous))
        return metadata

    def upgrade_remote_codex(self, profile: SSHProfile) -> dict[str, Any]:
        self._ensure_remote_helper(profile)
        previous = self.status(profile)
        result = self.runner.run(
            self._build_remote_exec_command(
                profile,
                self._build_upgrade_codex_script(profile),
            ),
            text=True,
            capture_output=True,
        )
        metadata = _parse_metadata(result.stdout)
        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip() or "unknown remote error"
            raise RuntimeError(f"failed to upgrade remote Codex runtime: {details}")
        metadata.update(self._restart_after_codex_update(profile, previous))
        return metadata

    def login_remote_codex(
        self,
        profile: SSHProfile,
        *,
        api_key_env: str | None = None,
    ) -> dict[str, Any]:
        api_key = self._resolve_local_api_key(api_key_env or profile.api_key_env)
        result = self.runner.run(
            self._build_remote_exec_command(profile, self._build_login_codex_script(profile)),
            text=True,
            capture_output=True,
            input=api_key,
        )
        metadata = _parse_metadata(result.stdout)
        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip() or "unknown remote error"
            raise RuntimeError(f"failed to log remote Codex runtime in: {details}")
        return metadata

    def up(
        self,
        profile: SSHProfile,
        *,
        local_port: int | None = None,
        sync_local: bool = False,
        auto_login: bool = False,
        api_key_env: str | None = None,
    ) -> dict[str, Any]:
        self._ensure_remote_helper(profile)
        service_state = self._safe_service_status(profile)
        probe = self._probe_remote_codex_status(profile)
        install_action = ""
        local_install_action = ""
        auth_action = ""
        effective_sync_local = sync_local or profile.sync_local_on_up
        desired_version = profile.codex_version
        remote_version = _extract_codex_version_number(
            probe.get("codex_version") if isinstance(probe.get("codex_version"), str) else None
        )

        if probe.get("status") == "missing-codex":
            if not desired_version:
                raise RuntimeError(
                    "remote Codex is missing; run install-codex or save the profile with --codex-version"
                )
            installed = self._install_remote_codex_package(profile, f"@openai/codex@{desired_version}")
            install_action = str(installed.get("status", "installed"))
        elif desired_version and remote_version != desired_version:
            installed = self._install_remote_codex_package(profile, f"@openai/codex@{desired_version}")
            install_action = str(installed.get("status", "installed"))

        effective_auto_login = auto_login or profile.auto_login_on_up
        effective_api_key_env = api_key_env or profile.api_key_env
        if probe.get("auth_status") != "logged-in" and effective_auto_login:
            logged_in = self.login_remote_codex(profile, api_key_env=effective_api_key_env)
            auth_action = str(logged_in.get("auth_status", ""))

        if effective_sync_local:
            local_target_version = desired_version or remote_version
            if not local_target_version:
                raise RuntimeError("cannot sync local Codex without a target version")
            local_install_action = self._sync_local_codex(local_target_version)

        if self._systemd_available(service_state):
            if self._service_is_installed(service_state):
                runtime = (
                    service_state
                    if service_state.get("status") == "active"
                    else self.service_start(profile)
                )
            else:
                self.service_install(profile)
                runtime = self.service_start(profile)
        else:
            runtime = self.ensure_remote_app_server(profile)

        doctor = self.doctor(profile, local_port=local_port)
        return {
            "status": "ok",
            "install_action": install_action,
            "local_install_action": local_install_action,
            "auth_action": auth_action,
            "runtime": runtime.get("runtime", ""),
            "local_codex_version": doctor.get("local_codex_version", ""),
            "remote_codex_version": doctor.get("remote_codex_version", ""),
            "remote_auth_status": doctor.get("remote_auth_status", ""),
            "remote_port": doctor.get("remote_port", ""),
            "local_port": doctor.get("local_port", ""),
            "workspace": doctor.get("workspace", ""),
            "warning": doctor.get("warning", ""),
        }

    def ensure_remote_app_server(self, profile: SSHProfile) -> dict[str, Any]:
        self._ensure_remote_helper(profile)
        service_state = self._safe_service_status(profile)
        if self._service_is_installed(service_state):
            if service_state.get("status") == "active":
                return service_state
            return self.service_start(profile)
        result = self.runner.run(
            self._build_remote_exec_command(profile, self._build_helper_command(profile, "start")),
            text=True,
            capture_output=True,
        )
        metadata = _parse_metadata(result.stdout)
        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip() or "unknown remote error"
            raise RuntimeError(f"failed to start remote Codex app-server: {details}")
        return metadata

    def status(self, profile: SSHProfile) -> dict[str, Any]:
        self._ensure_remote_helper(profile)
        service_state = self._safe_service_status(profile)
        if self._service_is_installed(service_state):
            return service_state
        result = self.runner.run(
            self._build_remote_exec_command(profile, self._build_helper_command(profile, "status")),
            text=True,
            capture_output=True,
        )
        metadata = _parse_metadata(result.stdout)
        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip() or "unknown remote error"
            raise RuntimeError(f"failed to inspect remote Codex app-server: {details}")
        return metadata

    def stop(self, profile: SSHProfile) -> dict[str, Any]:
        self._ensure_remote_helper(profile)
        service_state = self._safe_service_status(profile)
        if self._service_is_installed(service_state):
            if service_state.get("status") == "inactive":
                return service_state
            return self.service_stop(profile)
        result = self.runner.run(
            self._build_remote_exec_command(profile, self._build_helper_command(profile, "stop")),
            text=True,
            capture_output=True,
        )
        metadata = _parse_metadata(result.stdout)
        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip() or "unknown remote error"
            raise RuntimeError(f"failed to stop remote Codex app-server: {details}")
        return metadata

    def probe_remote_codex(self, profile: SSHProfile) -> dict[str, Any]:
        result, metadata = self._probe_remote_codex(profile)
        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip() or "unknown remote error"
            raise RuntimeError(f"failed to inspect remote Codex runtime: {details}")
        return metadata

    def read_remote_logs(self, profile: SSHProfile, *, lines: int = 200) -> str:
        self._ensure_remote_helper(profile)
        result = self.runner.run(
            self._build_remote_exec_command(
                profile,
                self._build_helper_command(profile, "logs", str(max(1, int(lines)))),
            ),
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip() or "unknown remote error"
            raise RuntimeError(f"failed to read remote Codex logs: {details}")
        return result.stdout

    def service_install(self, profile: SSHProfile) -> dict[str, Any]:
        self._ensure_remote_helper(profile)
        result = self.runner.run(
            self._build_remote_exec_command(
                profile,
                self._build_helper_command(profile, "install-service"),
            ),
            text=True,
            capture_output=True,
        )
        metadata = _parse_metadata(result.stdout)
        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip() or "unknown remote error"
            raise RuntimeError(f"failed to install remote Codex service: {details}")
        return metadata

    def service_uninstall(self, profile: SSHProfile) -> dict[str, Any]:
        self._ensure_remote_helper(profile)
        result = self.runner.run(
            self._build_remote_exec_command(
                profile,
                self._build_helper_command(profile, "uninstall-service"),
            ),
            text=True,
            capture_output=True,
        )
        metadata = _parse_metadata(result.stdout)
        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip() or "unknown remote error"
            raise RuntimeError(f"failed to uninstall remote Codex service: {details}")
        return metadata

    def service_status(self, profile: SSHProfile) -> dict[str, Any]:
        self._ensure_remote_helper(profile)
        result = self.runner.run(
            self._build_remote_exec_command(
                profile,
                self._build_helper_command(profile, "service-status"),
            ),
            text=True,
            capture_output=True,
        )
        metadata = _parse_metadata(result.stdout)
        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip() or "unknown remote error"
            raise RuntimeError(f"failed to inspect remote Codex service: {details}")
        return metadata

    def service_start(self, profile: SSHProfile) -> dict[str, Any]:
        self._ensure_remote_helper(profile)
        result = self.runner.run(
            self._build_remote_exec_command(
                profile,
                self._build_helper_command(profile, "service-start"),
            ),
            text=True,
            capture_output=True,
        )
        metadata = _parse_metadata(result.stdout)
        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip() or "unknown remote error"
            raise RuntimeError(f"failed to start remote Codex service: {details}")
        return metadata

    def service_stop(self, profile: SSHProfile) -> dict[str, Any]:
        self._ensure_remote_helper(profile)
        result = self.runner.run(
            self._build_remote_exec_command(
                profile,
                self._build_helper_command(profile, "service-stop"),
            ),
            text=True,
            capture_output=True,
        )
        metadata = _parse_metadata(result.stdout)
        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip() or "unknown remote error"
            raise RuntimeError(f"failed to stop remote Codex service: {details}")
        return metadata

    def build_local_codex_command(
        self,
        profile: SSHProfile,
        remote_url: str,
        extra_args: list[str] | None = None,
    ) -> list[str]:
        args = [self.local_codex_binary, "--remote", remote_url]
        extra = list(extra_args or [])
        if profile.remote_root and not self._contains_cwd_override(extra):
            args.extend(["-C", profile.remote_root])
        args.extend(extra)
        return args

    def launch(
        self,
        profile: SSHProfile,
        *,
        extra_args: list[str] | None = None,
        local_port: int | None = None,
    ) -> int:
        status = self.ensure_remote_app_server(profile)
        local_port = local_port or find_free_local_port()
        remote_port = int(status.get("port", profile.codex_app_server_port))
        remote_url = f"ws://127.0.0.1:{local_port}"

        with self.open_tunnel(profile, local_port=local_port, remote_port=remote_port):
            command = self.build_local_codex_command(profile, remote_url, extra_args)
            result = self.runner.run(command)
        return int(result.returncode)

    def smoke(
        self,
        profile: SSHProfile,
        *,
        local_port: int | None = None,
    ) -> dict[str, Any]:
        previous = self.status(profile)
        started = self.ensure_remote_app_server(profile)
        local_port = local_port or find_free_local_port()
        remote_port = int(started.get("port", profile.codex_app_server_port))
        with self.open_tunnel(profile, local_port=local_port, remote_port=remote_port):
            pass
        if (
            previous.get("runtime") == "service"
            and previous.get("status") != "active"
            and started.get("runtime") == "service"
            and started.get("status") == "active"
        ):
            self.service_stop(profile)
        elif previous.get("status") != "running" and started.get("status") == "started":
            self.stop(profile)
        return {
            "status": "ok",
            "remote_status": started.get("status"),
            "runtime": started.get("runtime"),
            "local_port": local_port,
            "remote_port": remote_port,
            "workspace": started.get("workspace"),
        }

    def doctor(
        self,
        profile: SSHProfile,
        *,
        local_port: int | None = None,
    ) -> dict[str, Any]:
        local_version = self._read_local_codex_version()
        remote_runtime = self.probe_remote_codex(profile)
        smoke = self.smoke(profile, local_port=local_port)
        warning = ""
        local_version_number = _extract_codex_version_number(local_version)
        remote_version_number = _extract_codex_version_number(
            remote_runtime.get("codex_version")
            if isinstance(remote_runtime.get("codex_version"), str)
            else None
        )
        if remote_runtime.get("auth_status") != "logged-in":
            warning = "remote-auth-missing"
        elif (
            local_version_number is not None
            and remote_version_number is not None
            and local_version_number != remote_version_number
        ):
            warning = "local-remote-version-mismatch"
        return {
            "status": "ok",
            "local_codex_version": local_version,
            "remote_codex_path": remote_runtime.get("codex_path"),
            "remote_codex_version": remote_runtime.get("codex_version"),
            "remote_auth_status": remote_runtime.get("auth_status"),
            "remote_auth_message": remote_runtime.get("auth_message"),
            "remote_port": smoke.get("remote_port"),
            "local_port": smoke.get("local_port"),
            "workspace": smoke.get("workspace"),
            "warning": warning,
        }

    @contextmanager
    def open_tunnel(
        self,
        profile: SSHProfile,
        *,
        local_port: int,
        remote_port: int,
    ) -> Iterator[ProcessLike]:
        process = self.runner.popen(
            self._build_tunnel_command(profile, local_port, remote_port),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            if process.poll() is not None:
                _, stderr = process.communicate(timeout=1)
                raise RuntimeError(f"SSH tunnel exited before startup: {stderr.strip()}")
            self.wait_for_tunnel_ready("127.0.0.1", local_port, 10.0)
            yield process
        finally:
            self._terminate_process(process)

    def _build_remote_exec_command(self, profile: SSHProfile, script: str) -> list[str]:
        args = self._build_ssh_base_args(profile)
        remote_command = f"exec sh -lc {shlex.quote(script)}"
        args.extend(["-T", self._ssh_target(profile), remote_command])
        return args

    def _ensure_remote_helper(self, profile: SSHProfile) -> None:
        if not self.auto_bootstrap:
            return
        target_key = self._target_key(profile)
        if target_key in self._bootstrapped_targets:
            return
        self.bootstrap(profile)

    def _build_tunnel_command(
        self,
        profile: SSHProfile,
        local_port: int,
        remote_port: int,
    ) -> list[str]:
        args = self._build_ssh_base_args(profile)
        args.extend(
            [
                "-o",
                "ExitOnForwardFailure=yes",
                "-L",
                f"127.0.0.1:{local_port}:127.0.0.1:{remote_port}",
                "-N",
                self._ssh_target(profile),
            ]
        )
        return args

    def _build_ssh_base_args(self, profile: SSHProfile) -> list[str]:
        args = [
            self.ssh_binary,
            "-o",
            "BatchMode=yes",
            "-o",
            "ServerAliveInterval=30",
            "-o",
            "ServerAliveCountMax=3",
        ]
        if profile.username:
            args.extend(["-l", profile.username])
        if profile.port and (profile.port != 22 or not profile.ssh_config_host):
            args.extend(["-p", str(profile.port)])
        if profile.key_path:
            args.extend(["-i", str(Path(profile.key_path).expanduser())])
        return args

    @staticmethod
    def _ssh_target(profile: SSHProfile) -> str:
        return profile.ssh_config_host or profile.host

    @staticmethod
    def _target_key(profile: SSHProfile) -> str:
        return f"{profile.username or ''}@{profile.ssh_config_host or profile.host}:{profile.port}"

    @staticmethod
    def _contains_cwd_override(args: list[str]) -> bool:
        return any(arg in {"-C", "--cd"} for arg in args)

    @staticmethod
    def _remote_state_dir(profile: SSHProfile) -> str:
        return f"${{HOME}}/.codex/ssh-remote-control/codex/{_sanitize_profile_name(profile.name)}"

    @staticmethod
    def _remote_helper_path() -> str:
        return f"${{HOME}}/.local/bin/{REMOTE_HELPER_BASENAME}"

    def _build_helper_command(self, profile: SSHProfile, action: str, *extra_args: str) -> str:
        args = [
            action,
            _sanitize_profile_name(profile.name),
            profile.codex_binary,
            str(profile.codex_app_server_port),
            profile.service_scope,
            profile.remote_root or "",
            *extra_args,
        ]
        helper_path = self._remote_helper_path().replace("${HOME}", "$HOME")
        return (
            f'HELPER_PATH="{helper_path}"; '
            + 'exec "$HELPER_PATH" '
            + " ".join(shlex.quote(arg) for arg in args)
        )

    def _build_bootstrap_script(self) -> str:
        helper_path = self._remote_helper_path()
        helper_body = self._remote_helper_script_body()
        return f"""
set -eu
HELPER_PATH="{helper_path}"
HELPER_DIR="$(dirname "$HELPER_PATH")"
mkdir -p "$HELPER_DIR"
cat >"$HELPER_PATH" <<'__CODEX_REMOTE_HELPER__'
{helper_body}
__CODEX_REMOTE_HELPER__
chmod 700 "$HELPER_PATH"
echo "status=installed"
echo "helper_path=$HELPER_PATH"
echo "helper_version={REMOTE_HELPER_VERSION}"
""".strip()

    def _build_install_codex_script(self, profile: SSHProfile, package_spec: str) -> str:
        quoted_binary = shlex.quote(profile.codex_binary)
        quoted_package = shlex.quote(package_spec)
        return f"""
set -eu
if ! command -v npm >/dev/null 2>&1; then
  echo "status=missing-npm"
  exit 21
fi
npm install -g {quoted_package}
if ! command -v {quoted_binary} >/dev/null 2>&1; then
  echo "status=missing-codex"
  exit 10
fi
CODEX_PATH="$(command -v {quoted_binary})"
CODEX_VERSION="$({quoted_binary} --version | head -n 1)"
echo "status=installed"
echo "package_spec={package_spec}"
echo "codex_path=$CODEX_PATH"
echo "codex_version=$CODEX_VERSION"
""".strip()

    def _build_upgrade_codex_script(self, profile: SSHProfile) -> str:
        quoted_binary = shlex.quote(profile.codex_binary)
        return f"""
set -eu
if ! command -v {quoted_binary} >/dev/null 2>&1; then
  echo "status=missing-codex"
  exit 10
fi
{quoted_binary} --upgrade
CODEX_PATH="$(command -v {quoted_binary})"
CODEX_VERSION="$({quoted_binary} --version | head -n 1)"
echo "status=upgraded"
echo "codex_path=$CODEX_PATH"
echo "codex_version=$CODEX_VERSION"
""".strip()

    def _build_login_codex_script(self, profile: SSHProfile) -> str:
        quoted_binary = shlex.quote(profile.codex_binary)
        return f"""
set -eu
if ! command -v {quoted_binary} >/dev/null 2>&1; then
  echo "status=missing-codex"
  exit 10
fi
{quoted_binary} login --with-api-key >/dev/null
CODEX_PATH="$(command -v {quoted_binary})"
CODEX_VERSION="$({quoted_binary} --version | head -n 1)"
AUTH_OUTPUT="$({quoted_binary} login status 2>&1 || true)"
AUTH_STATUS="logged-out"
case "$AUTH_OUTPUT" in
  Logged\ in*)
    AUTH_STATUS="logged-in"
    ;;
esac
echo "status=ok"
echo "codex_path=$CODEX_PATH"
echo "codex_version=$CODEX_VERSION"
echo "auth_status=$AUTH_STATUS"
echo "auth_message=$AUTH_OUTPUT"
""".strip()

    def _probe_remote_codex(
        self,
        profile: SSHProfile,
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
        self._ensure_remote_helper(profile)
        result = self.runner.run(
            self._build_remote_exec_command(profile, self._build_helper_command(profile, "probe")),
            text=True,
            capture_output=True,
        )
        return result, _parse_metadata(result.stdout)

    def _probe_remote_codex_status(self, profile: SSHProfile) -> dict[str, Any]:
        _, metadata = self._probe_remote_codex(profile)
        return metadata

    def _install_remote_codex_package(self, profile: SSHProfile, package_spec: str) -> dict[str, Any]:
        result = self.runner.run(
            self._build_remote_exec_command(
                profile,
                self._build_install_codex_script(profile, package_spec),
            ),
            text=True,
            capture_output=True,
        )
        metadata = _parse_metadata(result.stdout)
        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip() or "unknown remote error"
            raise RuntimeError(f"failed to install remote Codex runtime: {details}")
        return metadata

    def _sync_local_codex(self, version: str) -> str:
        current_version = None
        try:
            current_version = _extract_codex_version_number(self._read_local_codex_version())
        except RuntimeError as exc:
            if "not found" not in str(exc):
                raise
        if current_version == version:
            return ""
        package_spec = f"@openai/codex@{version}"
        result = self.runner.run(
            ["npm", "install", "-g", package_spec],
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip() or "unknown local error"
            raise RuntimeError(f"failed to sync local Codex runtime: {details}")
        return "installed" if current_version is None else "updated"

    @staticmethod
    def _resolve_local_api_key(env_name: str | None) -> str:
        effective_env = env_name or "OPENAI_API_KEY"
        api_key = os.environ.get(effective_env, "")
        if not api_key:
            raise RuntimeError(f"missing local API key env: {effective_env}")
        return api_key

    def _remote_helper_script_body(self) -> str:
        return """#!/usr/bin/env sh
set -eu

if [ "$#" -lt 6 ]; then
  echo "usage: codex-remote-app-server <command> <profile_name> <codex_binary> <port> <service_scope> <workspace> [extra...]" >&2
  exit 64
fi

COMMAND="$1"
PROFILE_NAME="$2"
CODEX_BINARY="$3"
PORT="$4"
SERVICE_SCOPE="$5"
WORKSPACE="$6"
shift 6

STATE_DIR="${HOME}/.codex/ssh-remote-control/codex/${PROFILE_NAME}"
PID_FILE="$STATE_DIR/app-server.pid"
LOG_FILE="$STATE_DIR/app-server.log"
SERVICE_NAME="codex-app-server-${PROFILE_NAME}.service"
mkdir -p "$STATE_DIR"

resolve_service_scope() {
  requested="$1"
  if [ "$requested" = "auto" ]; then
    if [ "$(id -u)" = "0" ]; then
      echo "system"
    else
      echo "user"
    fi
    return
  fi
  echo "$requested"
}

SERVICE_SCOPE_EFFECTIVE="$(resolve_service_scope "$SERVICE_SCOPE")"
if [ "$SERVICE_SCOPE_EFFECTIVE" = "user" ]; then
  UNIT_DIR="${HOME}/.config/systemd/user"
  UNIT_PATH="${UNIT_DIR}/${SERVICE_NAME}"
  SYSTEMCTL_SCOPE="--user"
  WANTED_BY_TARGET="default.target"
else
  UNIT_DIR="/etc/systemd/system"
  UNIT_PATH="${UNIT_DIR}/${SERVICE_NAME}"
  SYSTEMCTL_SCOPE=""
  WANTED_BY_TARGET="multi-user.target"
fi

run_systemctl() {
  if [ "$SERVICE_SCOPE_EFFECTIVE" = "user" ]; then
    uid="$(id -u)"
    export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/${uid}}"
    systemctl --user "$@"
  else
    systemctl "$@"
  fi
}

print_state() {
  current_status="$1"
  current_pid="$2"
  echo "runtime=process"
  echo "status=$current_status"
  echo "pid=$current_pid"
  echo "port=$PORT"
  echo "log_file=$LOG_FILE"
  echo "workspace=$WORKSPACE"
}

print_service_state() {
  current_status="$1"
  current_enabled="$2"
  echo "runtime=service"
  echo "status=$current_status"
  echo "port=$PORT"
  echo "enabled=$current_enabled"
  echo "service_name=$SERVICE_NAME"
  echo "service_scope=$SERVICE_SCOPE_EFFECTIVE"
  echo "unit_path=$UNIT_PATH"
  echo "log_file=$LOG_FILE"
  echo "workspace=$WORKSPACE"
}

case "$COMMAND" in
  probe)
    if ! command -v "$CODEX_BINARY" >/dev/null 2>&1; then
      echo "status=missing-codex"
      exit 10
    fi
    CODEX_PATH="$(command -v "$CODEX_BINARY")"
    CODEX_VERSION="$("$CODEX_BINARY" --version | head -n 1)"
    AUTH_OUTPUT="$("$CODEX_BINARY" login status 2>&1 || true)"
    AUTH_STATUS="logged-out"
    case "$AUTH_OUTPUT" in
      Logged\ in*)
        AUTH_STATUS="logged-in"
        ;;
    esac
    echo "status=ok"
    echo "codex_path=$CODEX_PATH"
    echo "codex_version=$CODEX_VERSION"
    echo "auth_status=$AUTH_STATUS"
    echo "auth_message=$AUTH_OUTPUT"
    ;;
  start)
    if ! command -v "$CODEX_BINARY" >/dev/null 2>&1; then
      print_state "missing-codex" ""
      exit 10
    fi
    app_state=""
    pid=""
    if [ -f "$PID_FILE" ]; then
      pid="$(cat "$PID_FILE" 2>/dev/null || true)"
      if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        app_state="already-running"
      else
        rm -f "$PID_FILE"
      fi
    fi
    if [ "$app_state" != "already-running" ]; then
      nohup "$CODEX_BINARY" app-server --listen "ws://127.0.0.1:$PORT" >>"$LOG_FILE" 2>&1 </dev/null &
      pid="$!"
      echo "$pid" >"$PID_FILE"
      sleep 1
      if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        app_state="started"
      else
        app_state="failed"
      fi
    fi
    print_state "$app_state" "$pid"
    if [ "$app_state" = "failed" ]; then
      exit 11
    fi
    ;;
  status)
    app_state="stopped"
    pid=""
    if [ -f "$PID_FILE" ]; then
      pid="$(cat "$PID_FILE" 2>/dev/null || true)"
      if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        app_state="running"
      fi
    fi
    print_state "$app_state" "$pid"
    ;;
  stop)
    app_state="already-stopped"
    pid=""
    if [ -f "$PID_FILE" ]; then
      pid="$(cat "$PID_FILE" 2>/dev/null || true)"
      if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        kill "$pid"
        rm -f "$PID_FILE"
        app_state="stopped"
      else
        rm -f "$PID_FILE"
      fi
    fi
    print_state "$app_state" "$pid"
    ;;
  logs)
    lines="${1:-200}"
    if [ -f "$LOG_FILE" ]; then
      tail -n "$lines" "$LOG_FILE"
    fi
    ;;
  install-service)
    if ! command -v systemctl >/dev/null 2>&1; then
      echo "status=missing-systemd"
      exit 20
    fi
    if [ "$SERVICE_SCOPE_EFFECTIVE" = "system" ] && [ "$(id -u)" != "0" ]; then
      echo "status=systemd-requires-root"
      exit 21
    fi
    if ! command -v "$CODEX_BINARY" >/dev/null 2>&1; then
      print_service_state "missing-codex" "no"
      exit 10
    fi
    mkdir -p "$UNIT_DIR"
    cat >"$UNIT_PATH" <<__CODEX_REMOTE_UNIT__
[Unit]
Description=Codex app-server for profile ${PROFILE_NAME}
After=network.target

[Service]
Type=simple
WorkingDirectory=${WORKSPACE:-/root}
ExecStart=/bin/sh -lc 'exec "$CODEX_BINARY" app-server --listen "ws://127.0.0.1:${PORT}"'
Restart=on-failure
RestartSec=2
StandardOutput=append:${LOG_FILE}
StandardError=append:${LOG_FILE}

[Install]
WantedBy=${WANTED_BY_TARGET}
__CODEX_REMOTE_UNIT__
    run_systemctl daemon-reload >/dev/null
    run_systemctl enable "$SERVICE_NAME" >/dev/null
    enabled="$(run_systemctl is-enabled "$SERVICE_NAME" 2>/dev/null || true)"
    print_service_state "installed" "$enabled"
    ;;
  uninstall-service)
    if ! command -v systemctl >/dev/null 2>&1; then
      echo "status=missing-systemd"
      exit 20
    fi
    run_systemctl stop "$SERVICE_NAME" >/dev/null 2>&1 || true
    run_systemctl disable "$SERVICE_NAME" >/dev/null 2>&1 || true
    rm -f "$UNIT_PATH"
    run_systemctl daemon-reload >/dev/null
    run_systemctl reset-failed "$SERVICE_NAME" >/dev/null 2>&1 || true
    print_service_state "uninstalled" "no"
    ;;
  service-status)
    if ! command -v systemctl >/dev/null 2>&1; then
      echo "status=missing-systemd"
      exit 20
    fi
    active="$(run_systemctl is-active "$SERVICE_NAME" 2>/dev/null || true)"
    enabled="$(run_systemctl is-enabled "$SERVICE_NAME" 2>/dev/null || true)"
    if [ ! -f "$UNIT_PATH" ]; then
      active="not-installed"
      enabled="no"
    fi
    print_service_state "$active" "$enabled"
    ;;
  service-start)
    if ! command -v systemctl >/dev/null 2>&1; then
      echo "status=missing-systemd"
      exit 20
    fi
    run_systemctl start "$SERVICE_NAME"
    active="$(run_systemctl is-active "$SERVICE_NAME" 2>/dev/null || true)"
    enabled="$(run_systemctl is-enabled "$SERVICE_NAME" 2>/dev/null || true)"
    print_service_state "$active" "$enabled"
    ;;
  service-stop)
    if ! command -v systemctl >/dev/null 2>&1; then
      echo "status=missing-systemd"
      exit 20
    fi
    run_systemctl stop "$SERVICE_NAME"
    active="$(run_systemctl is-active "$SERVICE_NAME" 2>/dev/null || true)"
    enabled="$(run_systemctl is-enabled "$SERVICE_NAME" 2>/dev/null || true)"
    print_service_state "$active" "$enabled"
    ;;
  *)
    echo "unknown command: $COMMAND" >&2
    exit 64
    ;;
esac
"""

    def _read_local_codex_version(self) -> str:
        if shutil.which(self.local_codex_binary) is None:
            raise RuntimeError(f"local Codex binary not found: {self.local_codex_binary}")
        result = self.runner.run(
            [self.local_codex_binary, "--version"],
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip() or "unknown local error"
            raise RuntimeError(f"failed to inspect local Codex runtime: {details}")
        return result.stdout.strip()

    @staticmethod
    def _service_is_installed(metadata: dict[str, Any] | None) -> bool:
        if not metadata:
            return False
        status = str(metadata.get("status", ""))
        return bool(metadata.get("service_name")) and status not in {
            "",
            "missing-systemd",
            "not-installed",
            "uninstalled",
        }

    def _safe_service_status(self, profile: SSHProfile) -> dict[str, Any] | None:
        try:
            return self.service_status(profile)
        except RuntimeError:
            return None

    @staticmethod
    def _systemd_available(metadata: dict[str, Any] | None) -> bool:
        if not metadata:
            return False
        return str(metadata.get("status", "")) != "missing-systemd"

    def _restart_after_codex_update(
        self,
        profile: SSHProfile,
        previous: dict[str, Any],
    ) -> dict[str, Any]:
        if previous.get("runtime") == "service" and previous.get("status") == "active":
            self.service_stop(profile)
            restarted = self.service_start(profile)
            return {"restart_status": restarted.get("status", "")}
        if previous.get("status") == "running":
            self.stop(profile)
            restarted = self.ensure_remote_app_server(profile)
            return {"restart_status": restarted.get("status", "")}
        return {"restart_status": ""}

    @staticmethod
    def _terminate_process(process: ProcessLike) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)

import io
import os
import posixpath
import stat
import tempfile
import unittest
from pathlib import Path

from ssh_remote_control.models import AuthMode, ConnectionStatus, PasswordStorage, SSHProfile
from ssh_remote_control.session_manager import SessionManager


class FakeAttr:
    def __init__(self, filename: str, is_dir: bool) -> None:
        self.filename = filename
        self.st_mode = stat.S_IFDIR if is_dir else stat.S_IFREG


class FakeSFTPClient:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.directories = {"/", "/srv", "/srv/app"}
        self.closed = False

    def file(self, path: str, mode: str):
        sftp = self

        class Handle:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def write(self, data: bytes) -> None:
                sftp.directories.add(posixpath.dirname(path) or "/")
                sftp.files[path] = data

            def read(self) -> bytes:
                return sftp.files[path]

        return Handle()

    def put(self, local_path: str, remote_path: str) -> None:
        self.directories.add(posixpath.dirname(remote_path) or "/")
        self.files[remote_path] = Path(local_path).read_bytes()

    def get(self, remote_path: str, local_path: str) -> None:
        target = Path(local_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.files[remote_path])

    def mkdir(self, path: str) -> None:
        self.directories.add(path)

    def stat(self, path: str):
        if path in self.directories:
            return FakeAttr(posixpath.basename(path), True)
        if path in self.files:
            return FakeAttr(posixpath.basename(path), False)
        raise FileNotFoundError(path)

    def listdir_attr(self, path: str):
        prefix = path.rstrip("/")
        results = []
        for directory in sorted(self.directories):
            if directory in {path, prefix, "/", ""}:
                continue
            parent = posixpath.dirname(directory.rstrip("/")) or "/"
            if parent == prefix:
                results.append(FakeAttr(posixpath.basename(directory), True))
        for filename in sorted(self.files):
            parent = posixpath.dirname(filename) or "/"
            if parent == prefix:
                results.append(FakeAttr(posixpath.basename(filename), False))
        return results

    def close(self) -> None:
        self.closed = True


class FakeChannel:
    def __init__(self, exit_status: int) -> None:
        self._exit_status = exit_status

    def recv_exit_status(self) -> int:
        return self._exit_status


class FakeSSHClient:
    def __init__(self) -> None:
        self.connected = False
        self.closed = False
        self.connect_kwargs = None
        self.sftp = FakeSFTPClient()
        self.exec_history: list[tuple[str, dict | None]] = []
        self.loaded_system_host_keys = False

    def connect(self, **kwargs) -> None:
        self.connected = True
        self.connect_kwargs = kwargs

    def load_system_host_keys(self) -> None:
        self.loaded_system_host_keys = True

    def open_sftp(self) -> FakeSFTPClient:
        return self.sftp

    def exec_command(self, command: str, environment=None):
        self.exec_history.append((command, environment))
        stdout = io.BytesIO(b"done\n")
        stderr = io.BytesIO(b"")
        stdout.channel = FakeChannel(0)
        return io.BytesIO(b""), stdout, stderr

    def close(self) -> None:
        self.closed = True


class SessionManagerTests(unittest.TestCase):
    def _profile(self) -> SSHProfile:
        return SSHProfile(
            name="prod",
            host="example.com",
            port=22,
            username="deploy",
            remote_root="/srv/app",
            auth_mode=AuthMode.PASSWORD,
            password_storage=PasswordStorage.SESSION_ONLY,
            ssh_config_host=None,
            key_path=None,
        )

    def test_connect_status_and_disconnect_profile(self) -> None:
        fake_client = FakeSSHClient()
        manager = SessionManager(client_factory=lambda: fake_client)

        manager.connect_profile(self._profile(), password="secret")

        status = manager.connection_status("prod")
        self.assertEqual(status, ConnectionStatus.CONNECTED)
        self.assertTrue(fake_client.loaded_system_host_keys)
        self.assertEqual(fake_client.connect_kwargs["hostname"], "example.com")
        self.assertEqual(fake_client.connect_kwargs["password"], "secret")

        manager.disconnect_profile("prod")

        self.assertEqual(manager.connection_status("prod"), ConnectionStatus.DISCONNECTED)
        self.assertTrue(fake_client.closed)
        self.assertTrue(fake_client.sftp.closed)

    def test_write_and_read_remote_text_file_use_connected_sftp_client(self) -> None:
        fake_client = FakeSSHClient()
        manager = SessionManager(client_factory=lambda: fake_client)
        manager.connect_profile(self._profile(), password="secret")

        manager.write_text_file("prod", "notes.txt", "hello world")

        self.assertEqual(manager.read_text_file("prod", "notes.txt"), "hello world")

    def test_run_command_executes_inside_remote_root(self) -> None:
        fake_client = FakeSSHClient()
        manager = SessionManager(client_factory=lambda: fake_client)
        manager.connect_profile(self._profile(), password="secret")

        result = manager.run_command("prod", "ls -la")

        self.assertEqual(result["exit_status"], 0)
        self.assertEqual(result["stdout"], "done\n")
        self.assertEqual(fake_client.exec_history[0][0], "cd /srv/app && ls -la")

    def test_upload_download_and_sync_directory_round_trip(self) -> None:
        fake_client = FakeSSHClient()
        manager = SessionManager(client_factory=lambda: fake_client)
        manager.connect_profile(self._profile(), password="secret")

        with tempfile.TemporaryDirectory() as tmp:
            local_root = Path(tmp) / "local"
            local_root.mkdir()
            nested = local_root / "nested"
            nested.mkdir()
            source_file = nested / "hello.txt"
            source_file.write_text("hello")

            manager.upload_path("prod", local_root, "release")
            self.assertEqual(
                fake_client.sftp.files["/srv/app/release/nested/hello.txt"],
                b"hello",
            )

            download_root = Path(tmp) / "download"
            manager.download_path("prod", "release", download_root)
            self.assertEqual(
                (download_root / "nested" / "hello.txt").read_text(),
                "hello",
            )

            second_local = Path(tmp) / "second"
            second_local.mkdir()
            (second_local / "sync.txt").write_text("sync")
            manager.sync_path("prod", second_local, "sync-dir", direction="upload")
            self.assertEqual(
                fake_client.sftp.files["/srv/app/sync-dir/sync.txt"],
                b"sync",
            )


if __name__ == "__main__":
    unittest.main()

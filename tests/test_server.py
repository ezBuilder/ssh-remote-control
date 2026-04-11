import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ssh_remote_control import server
from ssh_remote_control.models import AuthMode, ConnectionStatus, PasswordStorage, SSHProfile
from ssh_remote_control.profile_store import ProfileStore


class FakeCredentialStore:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def save_secret(
        self,
        profile_name: str,
        secret_kind: str,
        secret_value: str,
        storage: PasswordStorage,
    ) -> None:
        self.values[(profile_name, secret_kind)] = secret_value

    def load_secret(
        self,
        profile_name: str,
        secret_kind: str,
        storage: PasswordStorage,
    ) -> str | None:
        return self.values.get((profile_name, secret_kind))


class FakeSessionManager:
    def __init__(self) -> None:
        self.connected_profiles: set[str] = set()
        self.connect_calls: list[tuple[SSHProfile, str | None, str | None]] = []
        self.exec_calls: list[tuple[str, str, str | None, dict[str, str] | None]] = []
        self.read_calls: list[tuple[str, str]] = []
        self.write_calls: list[tuple[str, str, str]] = []
        self.upload_calls: list[tuple[str, str, str]] = []
        self.download_calls: list[tuple[str, str, str]] = []
        self.sync_calls: list[tuple[str, str, str, str]] = []

    def connect_profile(
        self,
        profile: SSHProfile,
        *,
        password: str | None = None,
        passphrase: str | None = None,
    ) -> None:
        self.connected_profiles.add(profile.name)
        self.connect_calls.append((profile, password, passphrase))

    def disconnect_profile(self, profile_name: str) -> None:
        self.connected_profiles.discard(profile_name)

    def connection_status(self, profile_name: str) -> ConnectionStatus:
        if profile_name in self.connected_profiles:
            return ConnectionStatus.CONNECTED
        return ConnectionStatus.DISCONNECTED

    def run_command(
        self,
        profile_name: str,
        command: str,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> dict[str, object]:
        self.exec_calls.append((profile_name, command, cwd, env))
        return {"command": command, "cwd": cwd, "stdout": "", "stderr": "", "exit_status": 0}

    def read_text_file(self, profile_name: str, path: str) -> str:
        self.read_calls.append((profile_name, path))
        return "content"

    def write_text_file(self, profile_name: str, path: str, content: str) -> None:
        self.write_calls.append((profile_name, path, content))

    def upload_path(self, profile_name: str, local_path: str, remote_path: str) -> None:
        self.upload_calls.append((profile_name, local_path, remote_path))

    def download_path(self, profile_name: str, remote_path: str, local_path: str) -> None:
        self.download_calls.append((profile_name, remote_path, local_path))

    def sync_path(
        self,
        profile_name: str,
        local_path: str,
        remote_path: str,
        *,
        direction: str = "upload",
    ) -> None:
        self.sync_calls.append((profile_name, local_path, remote_path, direction))


class ServerSecurityTests(unittest.TestCase):
    def _profile(self, **overrides: object) -> SSHProfile:
        base = SSHProfile(
            name="prod",
            host="example.com",
            port=22,
            username="deploy",
            remote_root="/srv/app",
            auth_mode=AuthMode.PASSWORD,
            password_storage=PasswordStorage.SESSION_ONLY,
        )
        values = base.to_dict()
        values.update(overrides)
        return SSHProfile.from_dict(values)

    def test_connect_requires_confirmation_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(Path(tmp) / "profiles.json")
            store.save_profile(self._profile())

            with patch.object(server, "profile_store", store), patch.object(
                server, "session_manager", FakeSessionManager()
            ), patch.object(server, "credential_store", FakeCredentialStore()):
                with self.assertRaisesRegex(ValueError, "confirm"):
                    server.ssh_connect("prod")

    def test_connect_accepts_confirmed_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(Path(tmp) / "profiles.json")
            fake_session_manager = FakeSessionManager()
            store.save_profile(self._profile())

            with patch.object(server, "profile_store", store), patch.object(
                server, "session_manager", fake_session_manager
            ), patch.object(server, "credential_store", FakeCredentialStore()):
                result = server.ssh_connect("prod", confirm=True)

            self.assertTrue(result["connected"])
            self.assertEqual(len(fake_session_manager.connect_calls), 1)

    def test_exec_requires_confirmation_unless_command_is_allowlisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(Path(tmp) / "profiles.json")
            fake_session_manager = FakeSessionManager()
            store.save_profile(
                self._profile(allowed_exec_prefixes=["git status", "systemctl status"])
            )

            with patch.object(server, "profile_store", store), patch.object(
                server, "session_manager", fake_session_manager
            ), patch.object(server, "credential_store", FakeCredentialStore()):
                with self.assertRaisesRegex(ValueError, "confirm"):
                    server.ssh_exec("prod", "uname -a")

                result = server.ssh_exec("prod", "git status")

            self.assertEqual(result["exit_status"], 0)
            self.assertEqual(fake_session_manager.exec_calls[0][1], "git status")

    def test_read_and_write_require_confirmation_unless_roots_are_allowlisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(Path(tmp) / "profiles.json")
            fake_session_manager = FakeSessionManager()
            store.save_profile(
                self._profile(
                    allowed_read_roots=["configs", "/srv/app/allowed-read"],
                    allowed_write_roots=["releases"],
                )
            )

            with patch.object(server, "profile_store", store), patch.object(
                server, "session_manager", fake_session_manager
            ), patch.object(server, "credential_store", FakeCredentialStore()):
                self.assertEqual(
                    server.ssh_read_file("prod", "configs/app.env")["content"],
                    "content",
                )
                with self.assertRaisesRegex(ValueError, "confirm"):
                    server.ssh_read_file("prod", "secrets.env")

                server.ssh_write_file("prod", "releases/build.txt", "ok")
                with self.assertRaisesRegex(ValueError, "confirm"):
                    server.ssh_write_file("prod", "tmp/build.txt", "nope")

            self.assertEqual(fake_session_manager.read_calls[0], ("prod", "configs/app.env"))
            self.assertEqual(
                fake_session_manager.write_calls[0],
                ("prod", "releases/build.txt", "ok"),
            )

    def test_allowlisted_roots_do_not_permit_parent_directory_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(Path(tmp) / "profiles.json")
            fake_session_manager = FakeSessionManager()
            store.save_profile(
                self._profile(
                    allowed_read_roots=[".", "configs"],
                    allowed_write_roots=[".", "releases"],
                )
            )

            with patch.object(server, "profile_store", store), patch.object(
                server, "session_manager", fake_session_manager
            ), patch.object(server, "credential_store", FakeCredentialStore()):
                with self.assertRaisesRegex(ValueError, "confirm"):
                    server.ssh_read_file("prod", "../outside.txt")

                with self.assertRaisesRegex(ValueError, "confirm"):
                    server.ssh_read_file("prod", "../configs/secrets.env")

                with self.assertRaisesRegex(ValueError, "confirm"):
                    server.ssh_write_file("prod", "../outside.txt", "blocked")

                with self.assertRaisesRegex(ValueError, "confirm"):
                    server.ssh_write_file("prod", "../releases/build.txt", "blocked")

    def test_profile_tool_output_does_not_expose_key_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(Path(tmp) / "profiles.json")

            with patch.object(server, "profile_store", store):
                result = server.ssh_profile_save(
                    name="prod",
                    host="example.com",
                    auth_mode=AuthMode.KEY_FILE.value,
                    key_path="~/.ssh/id_ed25519",
                    allowed_read_roots=["configs"],
                )

            profile_summary = result["profile"]
            self.assertNotIn("key_path", profile_summary)
            self.assertTrue(profile_summary["key_path_configured"])


if __name__ == "__main__":
    unittest.main()

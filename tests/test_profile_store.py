import json
import os
import tempfile
import unittest
from pathlib import Path

from ssh_remote_control.models import AuthMode, PasswordStorage, SSHProfile
from ssh_remote_control.profile_store import ProfileStore


class ProfileStoreTests(unittest.TestCase):
    def test_save_and_reload_profile_round_trips_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "profiles.json"
            store = ProfileStore(store_path)

            profile = SSHProfile(
                name="prod",
                host="example.com",
                port=22,
                username="deploy",
                remote_root="/srv/app",
                codex_binary="/opt/codex/bin/codex",
                codex_version="0.118.0",
                codex_app_server_port=4788,
                service_scope="user",
                auth_mode=AuthMode.PASSWORD,
                password_storage=PasswordStorage.KEYRING,
                ssh_config_host=None,
                key_path=None,
                default_model="gpt-5.4",
                default_cd="/srv/app-next",
                sync_local_on_up=True,
                api_key_env="OPENAI_API_KEY",
                auto_login_on_up=True,
                allow_connect_without_confirmation=True,
                allowed_exec_prefixes=["git status", "ls"],
                allowed_read_roots=["config", "/var/log/app"],
                allowed_write_roots=["releases", "/srv/app/tmp"],
            )

            store.save_profile(profile)
            reloaded = ProfileStore(store_path)

            loaded = reloaded.get_profile("prod")
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.host, "example.com")
            self.assertEqual(loaded.username, "deploy")
            self.assertEqual(loaded.remote_root, "/srv/app")
            self.assertEqual(loaded.codex_binary, "/opt/codex/bin/codex")
            self.assertEqual(loaded.codex_version, "0.118.0")
            self.assertEqual(loaded.codex_app_server_port, 4788)
            self.assertEqual(loaded.service_scope, "user")
            self.assertEqual(loaded.default_model, "gpt-5.4")
            self.assertEqual(loaded.default_cd, "/srv/app-next")
            self.assertTrue(loaded.sync_local_on_up)
            self.assertEqual(loaded.api_key_env, "OPENAI_API_KEY")
            self.assertTrue(loaded.auto_login_on_up)
            self.assertEqual(loaded.auth_mode, AuthMode.PASSWORD)
            self.assertEqual(loaded.password_storage, PasswordStorage.KEYRING)
            self.assertTrue(loaded.allow_connect_without_confirmation)
            self.assertEqual(loaded.allowed_exec_prefixes, ["git status", "ls"])
            self.assertEqual(loaded.allowed_read_roots, ["config", "/var/log/app"])
            self.assertEqual(loaded.allowed_write_roots, ["releases", "/srv/app/tmp"])

            payload = json.loads(store_path.read_text())
            self.assertIn("prod", payload["profiles"])

    def test_save_profile_restricts_permissions_on_posix(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX permission bits are not reliable on Windows")

        with tempfile.TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "state" / "profiles.json"
            store = ProfileStore(store_path)
            profile = SSHProfile(name="prod", host="example.com")

            store.save_profile(profile)

            file_mode = store_path.stat().st_mode & 0o777
            dir_mode = store_path.parent.stat().st_mode & 0o777
            self.assertEqual(file_mode, 0o600)
            self.assertEqual(dir_mode, 0o700)

    def test_delete_profile_removes_saved_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "profiles.json"
            store = ProfileStore(store_path)
            store.save_profile(SSHProfile(name="prod", host="example.com"))
            store.save_profile(SSHProfile(name="staging", host="staging.example.com"))

            deleted = store.delete_profile("prod")

            self.assertTrue(deleted)
            self.assertIsNone(store.get_profile("prod"))
            self.assertIsNotNone(store.get_profile("staging"))
            payload = json.loads(store_path.read_text())
            self.assertNotIn("prod", payload["profiles"])
            self.assertIn("staging", payload["profiles"])

    def test_first_saved_profile_becomes_default_and_can_be_switched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "profiles.json"
            store = ProfileStore(store_path)
            store.save_profile(SSHProfile(name="prod", host="example.com"))
            store.save_profile(SSHProfile(name="staging", host="staging.example.com"))

            self.assertEqual(store.get_default_profile_name(), "prod")
            store.set_default_profile("staging")

            self.assertEqual(store.get_default_profile_name(), "staging")
            reloaded = ProfileStore(store_path)
            self.assertEqual(reloaded.get_default_profile_name(), "staging")

    def test_deleting_default_profile_promotes_next_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "profiles.json"
            store = ProfileStore(store_path)
            store.save_profile(SSHProfile(name="prod", host="example.com"))
            store.save_profile(SSHProfile(name="staging", host="staging.example.com"))

            store.delete_profile("prod")

            self.assertEqual(store.get_default_profile_name(), "staging")

    def test_save_profile_rejects_relative_remote_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(Path(tmp) / "profiles.json")

            with self.assertRaisesRegex(ValueError, "remote_root must be an absolute POSIX path"):
                store.save_profile(
                    SSHProfile(
                        name="prod",
                        host="example.com",
                        remote_root="srv/app",
                    )
                )

    def test_save_profile_rejects_invalid_ports_and_env_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(Path(tmp) / "profiles.json")

            with self.assertRaisesRegex(ValueError, "port must be between 1 and 65535"):
                store.save_profile(SSHProfile(name="prod", host="example.com", port=0))

            with self.assertRaisesRegex(ValueError, "api_key_env must be a valid environment variable name"):
                store.save_profile(
                    SSHProfile(
                        name="prod",
                        host="example.com",
                        api_key_env="OPENAI-API-KEY",
                    )
                )

    def test_save_profile_rejects_parent_traversal_in_allowlists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(Path(tmp) / "profiles.json")

            with self.assertRaisesRegex(ValueError, "allowed_read_roots must not contain parent directory traversal"):
                store.save_profile(
                    SSHProfile(
                        name="prod",
                        host="example.com",
                        allowed_read_roots=["../secrets"],
                    )
                )

    def test_load_skips_invalid_profiles_and_keeps_valid_ones(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "profiles.json"
            store_path.write_text(
                json.dumps(
                    {
                        "default_profile": "broken",
                        "profiles": {
                            "broken": {
                                "name": "broken",
                                "host": "example.com",
                                "remote_root": "relative/path",
                            },
                            "prod": {
                                "name": "prod",
                                "host": "example.com",
                                "remote_root": "/srv/app",
                            },
                        },
                    }
                )
            )

            store = ProfileStore(store_path)

            self.assertIsNone(store.get_profile("broken"))
            self.assertIsNotNone(store.get_profile("prod"))
            self.assertEqual(store.get_default_profile_name(), "prod")
            self.assertEqual(store.get_load_warnings(), [{"name": "broken", "error": "remote_root must be an absolute POSIX path"}])

    def test_load_reports_invalid_json_as_store_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "profiles.json"
            store_path.write_text("{broken json")

            store = ProfileStore(store_path)

            self.assertEqual(store.list_profiles(), [])
            self.assertEqual(
                store.get_load_warnings(),
                [{"name": "<store>", "error": "invalid JSON: Expecting property name enclosed in double quotes"}],
            )

    def test_rewrite_clears_load_warnings_and_persists_valid_subset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "profiles.json"
            store_path.write_text(
                json.dumps(
                    {
                        "default_profile": "prod",
                        "profiles": {
                            "prod": {"name": "prod", "host": "example.com", "remote_root": "/srv/app"},
                            "broken": {"name": "broken", "host": "example.com", "remote_root": "relative/path"},
                        },
                    }
                )
            )

            store = ProfileStore(store_path)
            store.rewrite()

            self.assertEqual(store.get_load_warnings(), [])
            payload = json.loads(store_path.read_text())
            self.assertEqual(sorted(payload["profiles"].keys()), ["prod"])


if __name__ == "__main__":
    unittest.main()

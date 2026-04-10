import json
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
                auth_mode=AuthMode.PASSWORD,
                password_storage=PasswordStorage.KEYRING,
                ssh_config_host=None,
                key_path=None,
            )

            store.save_profile(profile)
            reloaded = ProfileStore(store_path)

            loaded = reloaded.get_profile("prod")
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.host, "example.com")
            self.assertEqual(loaded.username, "deploy")
            self.assertEqual(loaded.remote_root, "/srv/app")
            self.assertEqual(loaded.auth_mode, AuthMode.PASSWORD)
            self.assertEqual(loaded.password_storage, PasswordStorage.KEYRING)

            payload = json.loads(store_path.read_text())
            self.assertIn("prod", payload["profiles"])


if __name__ == "__main__":
    unittest.main()

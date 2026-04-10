import unittest

from ssh_remote_control.credential_store import CredentialStore
from ssh_remote_control.models import PasswordStorage


class FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service_name: str, username: str) -> str | None:
        return self.values.get((service_name, username))

    def set_password(self, service_name: str, username: str, password: str) -> None:
        self.values[(service_name, username)] = password

    def delete_password(self, service_name: str, username: str) -> None:
        self.values.pop((service_name, username), None)


class CredentialStoreTests(unittest.TestCase):
    def test_session_only_secret_round_trips_in_memory(self) -> None:
        store = CredentialStore(keyring_backend=None)

        store.save_secret("prod", "password", "secret", PasswordStorage.SESSION_ONLY)

        self.assertEqual(
            store.load_secret("prod", "password", PasswordStorage.SESSION_ONLY),
            "secret",
        )

    def test_keyring_secret_round_trips_through_backend(self) -> None:
        keyring = FakeKeyring()
        store = CredentialStore(keyring_backend=keyring)

        store.save_secret("prod", "password", "secret", PasswordStorage.KEYRING)

        self.assertEqual(
            store.load_secret("prod", "password", PasswordStorage.KEYRING),
            "secret",
        )


if __name__ == "__main__":
    unittest.main()

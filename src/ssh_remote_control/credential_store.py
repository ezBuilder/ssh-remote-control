from __future__ import annotations

from typing import Any

from ssh_remote_control.models import PasswordStorage


class CredentialStore:
    def __init__(
        self,
        *,
        keyring_backend: Any | None = None,
        service_name: str = "codex-ssh-remote-control",
    ) -> None:
        self._keyring_backend = keyring_backend or self._load_keyring_backend()
        self._service_name = service_name
        self._session_secrets: dict[tuple[str, str], str] = {}

    def save_secret(
        self,
        profile_name: str,
        secret_kind: str,
        secret_value: str,
        storage: PasswordStorage,
    ) -> None:
        if storage == PasswordStorage.SESSION_ONLY:
            self._session_secrets[(profile_name, secret_kind)] = secret_value
            return
        if storage == PasswordStorage.KEYRING:
            self._require_keyring().set_password(
                self._service_name,
                self._username(profile_name, secret_kind),
                secret_value,
            )

    def load_secret(
        self,
        profile_name: str,
        secret_kind: str,
        storage: PasswordStorage,
    ) -> str | None:
        if storage == PasswordStorage.SESSION_ONLY:
            return self._session_secrets.get((profile_name, secret_kind))
        if storage == PasswordStorage.KEYRING:
            return self._require_keyring().get_password(
                self._service_name,
                self._username(profile_name, secret_kind),
            )
        return None

    def clear_secret(
        self,
        profile_name: str,
        secret_kind: str,
        storage: PasswordStorage,
    ) -> None:
        self._session_secrets.pop((profile_name, secret_kind), None)
        if storage == PasswordStorage.KEYRING and self._keyring_backend is not None:
            self._keyring_backend.delete_password(
                self._service_name,
                self._username(profile_name, secret_kind),
            )

    @staticmethod
    def _load_keyring_backend() -> Any | None:
        try:
            import keyring  # type: ignore
        except ImportError:
            return None
        return keyring

    def _require_keyring(self) -> Any:
        if self._keyring_backend is None:
            raise RuntimeError("keyring support is unavailable in this environment")
        return self._keyring_backend

    @staticmethod
    def _username(profile_name: str, secret_kind: str) -> str:
        return f"{profile_name}:{secret_kind}"

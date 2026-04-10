from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from enum import Enum
from typing import Any


class AuthMode(str, Enum):
    AGENT = "agent"
    KEY_FILE = "key_file"
    PASSWORD = "password"
    SSH_CONFIG = "ssh_config"


class PasswordStorage(str, Enum):
    KEYRING = "keyring"
    NEVER = "never"
    SESSION_ONLY = "session_only"


class ConnectionStatus(str, Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"


@dataclass(slots=True)
class SSHProfile:
    name: str
    host: str
    port: int = 22
    username: str | None = None
    remote_root: str | None = None
    auth_mode: AuthMode = AuthMode.SSH_CONFIG
    password_storage: PasswordStorage = PasswordStorage.NEVER
    ssh_config_host: str | None = None
    key_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["auth_mode"] = self.auth_mode.value
        payload["password_storage"] = self.password_storage.value
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SSHProfile":
        return cls(
            name=payload["name"],
            host=payload["host"],
            port=int(payload.get("port", 22)),
            username=payload.get("username"),
            remote_root=payload.get("remote_root"),
            auth_mode=AuthMode(payload.get("auth_mode", AuthMode.SSH_CONFIG.value)),
            password_storage=PasswordStorage(
                payload.get("password_storage", PasswordStorage.NEVER.value)
            ),
            ssh_config_host=payload.get("ssh_config_host"),
            key_path=payload.get("key_path"),
        )

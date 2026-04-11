from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from enum import Enum
import re
from pathlib import PurePosixPath
from typing import Any


_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


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
    codex_binary: str = "codex"
    codex_version: str | None = None
    codex_app_server_port: int = 4500
    service_scope: str = "auto"
    default_model: str | None = None
    default_cd: str | None = None
    sync_local_on_up: bool = False
    api_key_env: str | None = None
    auto_login_on_up: bool = False
    auth_mode: AuthMode = AuthMode.SSH_CONFIG
    password_storage: PasswordStorage = PasswordStorage.NEVER
    ssh_config_host: str | None = None
    key_path: str | None = None
    allow_connect_without_confirmation: bool = False
    allowed_exec_prefixes: list[str] = field(default_factory=list)
    allowed_read_roots: list[str] = field(default_factory=list)
    allowed_write_roots: list[str] = field(default_factory=list)

    def validate(self) -> "SSHProfile":
        self.name = self._normalize_required("name", self.name)
        self.host = self._normalize_required("host", self.host)
        self.username = self._normalize_optional(self.username)
        self.remote_root = self._normalize_optional(self.remote_root)
        self.codex_binary = self._normalize_required("codex_binary", self.codex_binary)
        self.codex_version = self._normalize_optional(self.codex_version)
        self.service_scope = self._normalize_required("service_scope", self.service_scope)
        self.default_model = self._normalize_optional(self.default_model)
        self.default_cd = self._normalize_optional(self.default_cd)
        self.api_key_env = self._normalize_optional(self.api_key_env)
        self.ssh_config_host = self._normalize_optional(self.ssh_config_host)
        self.key_path = self._normalize_optional(self.key_path)
        self.allowed_exec_prefixes = self._normalize_string_list(
            "allowed_exec_prefixes",
            self.allowed_exec_prefixes,
        )
        self.allowed_read_roots = self._normalize_path_list(
            "allowed_read_roots",
            self.allowed_read_roots,
        )
        self.allowed_write_roots = self._normalize_path_list(
            "allowed_write_roots",
            self.allowed_write_roots,
        )

        if not _PROFILE_NAME_RE.match(self.name):
            raise ValueError(
                "profile name must contain only letters, digits, dots, underscores, or dashes"
            )
        self._validate_port("port", self.port)
        self._validate_port("codex_app_server_port", self.codex_app_server_port)
        if self.service_scope not in {"auto", "system", "user"}:
            raise ValueError("service_scope must be one of: auto, system, user")
        if self.remote_root is not None:
            self._validate_posix_absolute_path("remote_root", self.remote_root)
        if self.default_cd is not None:
            self._validate_posix_absolute_path("default_cd", self.default_cd)
        if self.api_key_env is not None and not _ENV_NAME_RE.match(self.api_key_env):
            raise ValueError("api_key_env must be a valid environment variable name")
        if self.auth_mode is AuthMode.KEY_FILE and self.key_path is None:
            raise ValueError("key_path is required when auth_mode=key_file")
        return self

    @staticmethod
    def _normalize_required(field_name: str, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{field_name} must not be empty")
        return normalized

    @staticmethod
    def _normalize_optional(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @classmethod
    def _normalize_string_list(cls, field_name: str, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            item = cls._normalize_optional(value)
            if item is None:
                raise ValueError(f"{field_name} must not contain empty entries")
            normalized.append(item)
        return normalized

    @classmethod
    def _normalize_path_list(cls, field_name: str, values: list[str]) -> list[str]:
        normalized = cls._normalize_string_list(field_name, values)
        for value in normalized:
            path = PurePosixPath(value)
            if ".." in path.parts:
                raise ValueError(f"{field_name} must not contain parent directory traversal")
        return normalized

    @staticmethod
    def _validate_port(field_name: str, value: int) -> None:
        if not 1 <= int(value) <= 65535:
            raise ValueError(f"{field_name} must be between 1 and 65535")

    @staticmethod
    def _validate_posix_absolute_path(field_name: str, value: str) -> None:
        path = PurePosixPath(value)
        if not path.is_absolute():
            raise ValueError(f"{field_name} must be an absolute POSIX path")
        if ".." in path.parts:
            raise ValueError(f"{field_name} must not contain parent directory traversal")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
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
            codex_binary=payload.get("codex_binary", "codex"),
            codex_version=payload.get("codex_version"),
            codex_app_server_port=int(payload.get("codex_app_server_port", 4500)),
            service_scope=payload.get("service_scope", "auto"),
            default_model=payload.get("default_model"),
            default_cd=payload.get("default_cd"),
            sync_local_on_up=bool(payload.get("sync_local_on_up", False)),
            api_key_env=payload.get("api_key_env"),
            auto_login_on_up=bool(payload.get("auto_login_on_up", False)),
            auth_mode=AuthMode(payload.get("auth_mode", AuthMode.SSH_CONFIG.value)),
            password_storage=PasswordStorage(
                payload.get("password_storage", PasswordStorage.NEVER.value)
            ),
            ssh_config_host=payload.get("ssh_config_host"),
            key_path=payload.get("key_path"),
            allow_connect_without_confirmation=bool(
                payload.get("allow_connect_without_confirmation", False)
            ),
            allowed_exec_prefixes=list(payload.get("allowed_exec_prefixes", [])),
            allowed_read_roots=list(payload.get("allowed_read_roots", [])),
            allowed_write_roots=list(payload.get("allowed_write_roots", [])),
        )

from __future__ import annotations

import json
from pathlib import Path

from ssh_remote_control.models import SSHProfile


class ProfileStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._profiles: dict[str, SSHProfile] = {}
        self._default_profile_name: str | None = None
        self._load_warnings: list[dict[str, str]] = []
        self._load()
        self._ensure_secure_permissions()

    def _load(self) -> None:
        if not self.path.exists():
            return
        self._load_warnings = []
        try:
            payload = json.loads(self.path.read_text())
        except json.JSONDecodeError as exc:
            self._load_warnings.append(
                {
                    "name": "<store>",
                    "error": f"invalid JSON: {exc.msg}",
                }
            )
            return
        loaded_profiles: dict[str, SSHProfile] = {}
        for name, profile_payload in payload.get("profiles", {}).items():
            try:
                loaded_profiles[name] = SSHProfile.from_dict(profile_payload).validate()
            except (KeyError, TypeError, ValueError) as exc:
                self._load_warnings.append(
                    {
                        "name": str(name),
                        "error": str(exc),
                    }
                )
                continue
        self._profiles = loaded_profiles
        default_profile_name = payload.get("default_profile")
        if isinstance(default_profile_name, str) and default_profile_name in self._profiles:
            self._default_profile_name = default_profile_name
        elif len(self._profiles) == 1:
            self._default_profile_name = next(iter(self._profiles))

    def save_profile(self, profile: SSHProfile) -> SSHProfile:
        profile.validate()
        self._profiles[profile.name] = profile
        if self._default_profile_name is None:
            self._default_profile_name = profile.name
        self._write()
        return profile

    def get_profile(self, name: str) -> SSHProfile | None:
        return self._profiles.get(name)

    def list_profiles(self) -> list[SSHProfile]:
        return list(sorted(self._profiles.values(), key=lambda profile: profile.name))

    def get_load_warnings(self) -> list[dict[str, str]]:
        return list(self._load_warnings)

    def get_default_profile_name(self) -> str | None:
        return self._default_profile_name

    def get_default_profile(self) -> SSHProfile | None:
        if self._default_profile_name is None:
            return None
        return self._profiles.get(self._default_profile_name)

    def set_default_profile(self, name: str) -> SSHProfile:
        profile = self.get_profile(name)
        if profile is None:
            raise KeyError(name)
        self._default_profile_name = name
        self._write()
        return profile

    def clear_default_profile(self) -> None:
        self._default_profile_name = None
        self._write()

    def delete_profile(self, name: str) -> bool:
        if name not in self._profiles:
            return False
        del self._profiles[name]
        if self._default_profile_name == name:
            self._default_profile_name = next(iter(sorted(self._profiles))) if self._profiles else None
        self._write()
        return True

    def rewrite(self) -> None:
        self._write()
        self._load_warnings = []

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "default_profile": self._default_profile_name,
            "profiles": {
                profile_name: stored_profile.to_dict()
                for profile_name, stored_profile in sorted(self._profiles.items())
            },
        }
        self.path.write_text(f"{json.dumps(payload, indent=2)}\n")
        self._ensure_secure_permissions()

    def _ensure_secure_permissions(self) -> None:
        if self.path.parent.exists():
            self._chmod_best_effort(self.path.parent, 0o700)
        if self.path.exists():
            self._chmod_best_effort(self.path, 0o600)

    @staticmethod
    def _chmod_best_effort(path: Path, mode: int) -> None:
        try:
            path.chmod(mode)
        except OSError:
            return

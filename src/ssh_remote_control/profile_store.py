from __future__ import annotations

import json
from pathlib import Path

from ssh_remote_control.models import SSHProfile


class ProfileStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._profiles: dict[str, SSHProfile] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        payload = json.loads(self.path.read_text())
        self._profiles = {
            name: SSHProfile.from_dict(profile_payload)
            for name, profile_payload in payload.get("profiles", {}).items()
        }

    def save_profile(self, profile: SSHProfile) -> SSHProfile:
        self._profiles[profile.name] = profile
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "profiles": {
                name: stored_profile.to_dict()
                for name, stored_profile in sorted(self._profiles.items())
            }
        }
        self.path.write_text(f"{json.dumps(payload, indent=2)}\n")
        return profile

    def get_profile(self, name: str) -> SSHProfile | None:
        return self._profiles.get(name)

    def list_profiles(self) -> list[SSHProfile]:
        return list(sorted(self._profiles.values(), key=lambda profile: profile.name))

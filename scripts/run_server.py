#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PLUGIN_ROOT / "src"
VENV_CANDIDATES = [
    PLUGIN_ROOT / ".venv" / "bin" / "python",
    PLUGIN_ROOT / ".venv" / "Scripts" / "python.exe",
]

for candidate in VENV_CANDIDATES:
    if candidate.exists() and Path(sys.executable).resolve() != candidate.resolve():
        os.execv(str(candidate), [str(candidate), __file__])

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

try:
    from ssh_remote_control.server import main
except ImportError as exc:
    raise SystemExit(
        "Missing Python dependencies for ssh-remote-control. "
        f"Run `python3 -m venv {PLUGIN_ROOT / '.venv'} && "
        f"{PLUGIN_ROOT / '.venv' / 'bin' / 'python'} -m pip install -e {PLUGIN_ROOT}` "
        "or install the package from its GitHub repository."
    ) from exc


if __name__ == "__main__":
    main()

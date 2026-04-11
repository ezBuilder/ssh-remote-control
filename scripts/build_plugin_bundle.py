from __future__ import annotations

import argparse
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED
from zipfile import ZipFile


REQUIRED_PATHS = [
    ".codex-plugin",
    ".mcp.json",
    "skills",
    "scripts",
    "src",
    "pyproject.toml",
    "README.md",
    "LICENSE",
]

EXCLUDED_DIR_NAMES = {
    "__pycache__",
    ".pytest_cache",
}
EXCLUDED_SUFFIXES = {
    ".pyc",
    ".pyo",
}
EXCLUDED_NAME_SUFFIXES = {
    ".egg-info",
}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _should_include(path: Path) -> bool:
    if any(part in EXCLUDED_DIR_NAMES for part in path.parts):
        return False
    if path.suffix in EXCLUDED_SUFFIXES:
        return False
    if any(
        part.endswith(suffix)
        for part in path.parts
        for suffix in EXCLUDED_NAME_SUFFIXES
    ):
        return False
    return True


def _iter_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path] if _should_include(path) else []
    return sorted(
        file_path
        for file_path in path.rglob("*")
        if file_path.is_file() and _should_include(file_path.relative_to(path))
    )


def build_bundle(output_path: Path, *, bundle_root: str = "ssh-remote-control") -> dict[str, object]:
    root = repo_root()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    included_files: list[str] = []

    with ZipFile(output_path, "w", compression=ZIP_DEFLATED) as archive:
        for relative in REQUIRED_PATHS:
            source = root / relative
            if not source.exists():
                raise FileNotFoundError(f"missing bundle path: {source}")
            for file_path in _iter_files(source):
                arcname = Path(bundle_root) / file_path.relative_to(root)
                archive.write(file_path, arcname.as_posix())
                included_files.append(str(file_path.relative_to(root)))

    return {
        "status": "ok",
        "bundle": str(output_path),
        "bundle_root": bundle_root,
        "files": included_files,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="build_plugin_bundle.py",
        description="Build a distributable zip bundle for the SSH Remote Control plugin.",
    )
    parser.add_argument(
        "--output",
        default=str(repo_root() / "dist" / "ssh-remote-control-plugin.zip"),
    )
    parser.add_argument("--bundle-root", default="ssh-remote-control")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    result = build_bundle(Path(args.output).expanduser(), bundle_root=args.bundle_root)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"status={result['status']}")
        print(f"bundle={result['bundle']}")
        print(f"bundle_root={result['bundle_root']}")
        print(f"file_count={len(result['files'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

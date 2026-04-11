#!/usr/bin/env sh
set -eu

usage() {
  cat <<'EOF' >&2
usage: install_local.sh [--bin-dir DIR] [--python PYTHON] [--venv-dir DIR] [--shell-completion SHELL] [--skip-install-cli]
EOF
}

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}
VENV_DIR="$REPO_DIR/.venv"
BIN_DIR="${HOME}/.local/bin"
SHELL_COMPLETION=""
SKIP_INSTALL_CLI=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --bin-dir)
      [ "$#" -ge 2 ] || {
        usage
        exit 64
      }
      BIN_DIR="$2"
      shift 2
      ;;
    --python)
      [ "$#" -ge 2 ] || {
        usage
        exit 64
      }
      PYTHON_BIN="$2"
      shift 2
      ;;
    --venv-dir)
      [ "$#" -ge 2 ] || {
        usage
        exit 64
      }
      VENV_DIR="$2"
      shift 2
      ;;
    --shell-completion)
      [ "$#" -ge 2 ] || {
        usage
        exit 64
      }
      SHELL_COMPLETION="$2"
      shift 2
      ;;
    --skip-install-cli)
      SKIP_INSTALL_CLI=1
      shift
      ;;
    *)
      usage
      exit 64
      ;;
  esac
done

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "python not found: $PYTHON_BIN" >&2
  exit 127
fi

if [ ! -x "$VENV_DIR/bin/python" ]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install -e "$REPO_DIR"

LAUNCHER_PATH=""
if [ "$SKIP_INSTALL_CLI" -eq 0 ]; then
  if [ -n "$SHELL_COMPLETION" ]; then
    "$VENV_DIR/bin/codex-remote" install-cli --bin-dir "$BIN_DIR" --shell-completion "$SHELL_COMPLETION"
  else
    "$VENV_DIR/bin/codex-remote" install-cli --bin-dir "$BIN_DIR"
  fi
  LAUNCHER_PATH="$BIN_DIR/codex-remote"
fi

printf 'status=ok\n'
printf 'repo=%s\n' "$REPO_DIR"
printf 'venv=%s\n' "$VENV_DIR"
if [ "$SKIP_INSTALL_CLI" -eq 0 ]; then
  printf 'bin_dir=%s\n' "$BIN_DIR"
  printf 'launcher=%s\n' "$LAUNCHER_PATH"
  if [ -n "$SHELL_COMPLETION" ]; then
    printf 'shell_completion=%s\n' "$SHELL_COMPLETION"
  fi
fi

#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SUPPORTED_PLATFORM="darwin-arm64"
DEFAULT_PYTHON_VERSION="3.12.13"
PYTHON_VERSION="${EIDOS_PYTHON_VERSION:-$DEFAULT_PYTHON_VERSION}"
BUILD_ROOT="$ROOT_DIR/build/macos-runtime"
PYTHON_ROOT="$BUILD_ROOT/python"
APP_ROOT="$BUILD_ROOT/app"
STAGING_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/eidos-runtime-build.XXXXXX")"

cleanup() {
  rm -rf -- "$STAGING_ROOT"
}
trap cleanup EXIT

cd "$ROOT_DIR"

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "macOS arm64 is required to build the $SUPPORTED_PLATFORM Runtime bundle" >&2
  exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required on the build machine" >&2
  exit 1
fi
if ! command -v rsync >/dev/null 2>&1; then
  echo "rsync is required on macOS to copy Runtime resources" >&2
  exit 1
fi
if ! command -v node >/dev/null 2>&1; then
  echo "Node.js is required to run the bundled Runtime smoke check" >&2
  exit 1
fi

rm -rf -- "$BUILD_ROOT"
mkdir -p "$PYTHON_ROOT" "$APP_ROOT"

PYTHON_INSTALL_ROOT="$STAGING_ROOT/python"
mkdir -p "$PYTHON_INSTALL_ROOT"
uv python install "$PYTHON_VERSION" \
  --install-dir "$PYTHON_INSTALL_ROOT" \
  --no-bin

PYTHON_SOURCE="$(find "$PYTHON_INSTALL_ROOT" -mindepth 1 -maxdepth 1 -type d -name 'cpython-*' -print | sort | head -n 1)"
if [[ -z "$PYTHON_SOURCE" ]]; then
  echo "uv did not produce a managed CPython installation for $PYTHON_VERSION" >&2
  exit 1
fi
rsync -a "$PYTHON_SOURCE/" "$PYTHON_ROOT/"

PYTHON_EXECUTABLE="$PYTHON_ROOT/bin/python3"
if [[ ! -x "$PYTHON_EXECUTABLE" ]]; then
  PYTHON_VERSIONED_EXECUTABLE=""
  for candidate in "$PYTHON_ROOT/bin"/python3.*; do
    if [[ -x "$candidate" && "$candidate" != *-config ]]; then
      PYTHON_VERSIONED_EXECUTABLE="$candidate"
      break
    fi
  done
  if [[ -z "$PYTHON_VERSIONED_EXECUTABLE" ]]; then
    echo "bundled CPython is missing a versioned executable" >&2
    exit 1
  fi
  ln -s "$(basename "$PYTHON_VERSIONED_EXECUTABLE")" "$PYTHON_EXECUTABLE"
fi

"$PYTHON_EXECUTABLE" --version

REQUIREMENTS_FILE="$STAGING_ROOT/requirements.txt"
uv -q export \
  --locked \
  --no-dev \
  --no-emit-project \
  --format requirements.txt \
  --output-file "$REQUIREMENTS_FILE" \
  --python "$PYTHON_EXECUTABLE"
if grep -Eq '^(pytest|ruff|deptry|pip-audit|pydevd)([<=>;[:space:]]|$)' "$REQUIREMENTS_FILE"; then
  echo "development dependency leaked into the production Runtime bundle" >&2
  exit 1
fi
uv pip install \
  --python "$PYTHON_EXECUTABLE" \
  --target "$APP_ROOT" \
  --link-mode copy \
  --requirements "$REQUIREMENTS_FILE"
rm -f -- "$APP_ROOT/.lock"

rsync -a \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  "$ROOT_DIR/runtime/eidos_runtime/" \
  "$APP_ROOT/eidos_runtime/"

required_resources=(
  "eidos_runtime/__main__.py"
  "eidos_runtime/sandbox/seatbelt.sbpl"
  "eidos_runtime/sandbox/file_commit_helper.py"
  "eidos_runtime/sandbox/sensitive_rules.json"
  "eidos_runtime/sandbox/mcp_connector.sbpl"
  "eidos_runtime/sandbox/mcp_workspace_read.sbpl"
  "eidos_runtime/extensions/mcp_launcher.py"
  "eidos_runtime/resources/bin/ripgrep/manifest.json"
  "eidos_runtime/resources/bin/ripgrep/darwin-arm64/rg"
  "eidos_runtime/resources/skills/.system"
)
for relative_path in "${required_resources[@]}"; do
  if [[ ! -e "$APP_ROOT/$relative_path" ]]; then
    echo "bundled Runtime resource is missing: $relative_path" >&2
    exit 1
  fi
done
if [[ ! -x "$APP_ROOT/eidos_runtime/resources/bin/ripgrep/darwin-arm64/rg" ]]; then
  echo "bundled Ripgrep lost its executable bit" >&2
  exit 1
fi
if ! cmp -s \
  "$ROOT_DIR/runtime/eidos_runtime/resources/bin/ripgrep/manifest.json" \
  "$APP_ROOT/eidos_runtime/resources/bin/ripgrep/manifest.json"; then
  echo "bundled Ripgrep manifest changed during packaging" >&2
  exit 1
fi
SOURCE_RG_SHA256="$(shasum -a 256 "$ROOT_DIR/runtime/eidos_runtime/resources/bin/ripgrep/darwin-arm64/rg" | awk '{print $1}')"
BUNDLED_RG_SHA256="$(shasum -a 256 "$APP_ROOT/eidos_runtime/resources/bin/ripgrep/darwin-arm64/rg" | awk '{print $1}')"
if [[ "$SOURCE_RG_SHA256" != "$BUNDLED_RG_SHA256" ]]; then
  echo "bundled Ripgrep SHA256 does not match the source artifact" >&2
  exit 1
fi

env -u EIDOS_PYTHON -u PYTHONHOME \
  PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH="$APP_ROOT" \
  "$PYTHON_EXECUTABLE" -c \
  'import anyio, eidos_runtime, httpx, mcp, openai, pydantic, pydantic_ai, pydantic_core, tree_sitter; print(eidos_runtime.__file__)'

node "$ROOT_DIR/scripts/bundled-runtime-smoke.mjs"

echo "Built self-contained $SUPPORTED_PLATFORM Runtime bundle at $BUILD_ROOT"

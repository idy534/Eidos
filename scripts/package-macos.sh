#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
RELEASE_DIR="$ROOT_DIR/release"
PACKAGE_TEMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/eidos-package.XXXXXX")"
DMG_MOUNT="$PACKAGE_TEMP_ROOT/dmg-mount"
MOUNTED=0

log() {
  printf '[package:mac] %s\n' "$*"
}

fail() {
  printf '[package:mac] ERROR: %s\n' "$*" >&2
  exit 1
}

cleanup() {
  if [[ "$MOUNTED" == "1" ]]; then
    hdiutil detach "$DMG_MOUNT" >/dev/null 2>&1 || true
  fi
  rm -rf -- "$PACKAGE_TEMP_ROOT"
}
trap cleanup EXIT

MODE="${1:-}"
case "$MODE" in
  local|release)
    ;;
  *)
    fail "invalid mode '$MODE'; expected local or release"
    ;;
esac

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  fail "macOS arm64 is required; packaging supports Darwin arm64 only"
fi

for command_name in node pnpm uv hdiutil ditto sips iconutil; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    fail "$command_name is required on the build machine"
  fi
done

if [[ "$MODE" == "release" ]]; then
  if [[ -z "${CSC_LINK:-}" ]] && [[ -z "${CSC_NAME:-}" ]]; then
    if ! command -v security >/dev/null 2>&1 \
      || ! security find-identity -v -p codesigning 2>/dev/null | grep -q "Developer ID Application"; then
      fail "release signing credentials are missing; set CSC_LINK (and CSC_KEY_PASSWORD), set CSC_NAME, or install a Developer ID Application certificate"
    fi
  fi

  if [[ -n "${APPLE_API_KEY:-}" && -n "${APPLE_API_KEY_ID:-}" && -n "${APPLE_API_ISSUER:-}" ]]; then
    :
  elif [[ -n "${APPLE_ID:-}" && -n "${APPLE_APP_SPECIFIC_PASSWORD:-}" && -n "${APPLE_TEAM_ID:-}" ]]; then
    :
  elif [[ -n "${APPLE_KEYCHAIN_PROFILE:-}" ]]; then
    :
  else
    fail "release notarization credentials are missing; set APPLE_API_KEY, APPLE_API_KEY_ID, APPLE_API_ISSUER (or APPLE_ID, APPLE_APP_SPECIFIC_PASSWORD, APPLE_TEAM_ID)"
  fi

  if [[ "${EIDOS_PACKAGE_SKIP_TESTS:-0}" == "1" ]]; then
    fail "EIDOS_PACKAGE_SKIP_TESTS=1 is not allowed for release packaging"
  fi
fi

VERSION="$(node -e 'const fs = require("node:fs"); process.stdout.write(JSON.parse(fs.readFileSync("package.json", "utf8")).version);')"
if [[ -z "$VERSION" ]]; then
  fail "package.json version is empty"
fi

log "mode=$MODE version=$VERSION architecture=arm64"
log "installing locked JavaScript and Python dependencies"
pnpm install --frozen-lockfile
uv sync --locked

if [[ "${EIDOS_PACKAGE_SKIP_TESTS:-0}" == "1" ]]; then
  log "WARNING: package validation skipped because EIDOS_PACKAGE_SKIP_TESTS=1"
else
  log "running packaging configuration and project validation"
  pnpm test:packaging
  pnpm lint:python
  pnpm deps:python
  pnpm test
  pnpm build
  pnpm test:seatbelt-native
  pnpm test:electron-smoke
fi

log "building and testing the self-contained Runtime bundle"
pnpm build:runtime:mac
pnpm test:runtime:bundled
pnpm test:runtime:bundled-seatbelt

log "building Electron application assets"
pnpm build

log "generating the native app icon from the repository Eidos logo"
bash "$ROOT_DIR/scripts/build-macos-icon.sh"

log "removing previous packaging artifacts from $RELEASE_DIR"
rm -rf -- "$RELEASE_DIR"
mkdir -p "$RELEASE_DIR" "$DMG_MOUNT"

if [[ "$MODE" == "local" ]]; then
  ARTIFACT_NAME="Eidos-${VERSION}-mac-arm64-local.dmg"
  log "building unsigned local DMG with electron-builder 26.15.3"
  CSC_IDENTITY_AUTO_DISCOVERY=false pnpm exec electron-builder \
    --config electron-builder.yml \
    --mac dmg \
    --arm64 \
    --publish never \
    --config.mac.identity=null \
    --config.mac.hardenedRuntime=false \
    --config.mac.notarize=false \
    --config.forceCodeSigning=false \
    --config.artifactName="$ARTIFACT_NAME"
else
  ARTIFACT_NAME="Eidos-${VERSION}-mac-arm64.dmg"
  log "building signed and notarized release DMG with electron-builder 26.15.3"
  CSC_IDENTITY_AUTO_DISCOVERY=true pnpm exec electron-builder \
    --config electron-builder.yml \
    --mac dmg \
    --arm64 \
    --publish never \
    --config.mac.hardenedRuntime=true \
    --config.mac.notarize=true \
    --config.forceCodeSigning=true \
    --config.artifactName="$ARTIFACT_NAME"
fi

DMG_PATH="$RELEASE_DIR/$ARTIFACT_NAME"
if [[ ! -s "$DMG_PATH" ]]; then
  fail "expected DMG was not created: $DMG_PATH"
fi
APP_PATH="$(find "$RELEASE_DIR" -type d -name 'Eidos.app' -print -quit)"
if [[ -z "$APP_PATH" || ! -d "$APP_PATH" ]]; then
  fail "electron-builder did not produce Eidos.app under $RELEASE_DIR"
fi

log "running packaged App and Runtime verification from the builder output"
node "$ROOT_DIR/scripts/packaged-electron-smoke.mjs" "$APP_PATH"

log "mounting the final DMG and testing a copied App outside the repository"
hdiutil attach -nobrowse -readonly -mountpoint "$DMG_MOUNT" "$DMG_PATH" >/dev/null
MOUNTED=1
if [[ ! -d "$DMG_MOUNT/Eidos.app" ]]; then
  fail "DMG does not contain Eidos.app"
fi
if [[ ! -L "$DMG_MOUNT/Applications" ]]; then
  fail "DMG does not contain the Applications symlink"
fi
COPIED_APP="$PACKAGE_TEMP_ROOT/copied/Eidos.app"
mkdir -p "$(dirname -- "$COPIED_APP")"
ditto "$DMG_MOUNT/Eidos.app" "$COPIED_APP"
hdiutil detach "$DMG_MOUNT" >/dev/null
MOUNTED=0
node "$ROOT_DIR/scripts/packaged-electron-smoke.mjs" "$COPIED_APP"

if [[ "$MODE" == "release" ]]; then
  log "verifying signed release App and stapled artifacts"
  codesign --verify --deep --strict --verbose=2 "$APP_PATH"
  spctl --assess --type execute --verbose=2 "$APP_PATH"
  xcrun stapler validate "$APP_PATH"
  xcrun stapler validate "$DMG_PATH"
fi

APP_ABSOLUTE_PATH="$(cd -- "$(dirname -- "$APP_PATH")" && pwd -P)/$(basename -- "$APP_PATH")"
DMG_ABSOLUTE_PATH="$(cd -- "$(dirname -- "$DMG_PATH")" && pwd -P)/$(basename -- "$DMG_PATH")"
DMG_SIZE="$(stat -f '%z' "$DMG_PATH")"
log "Eidos macOS package complete"
log "Mode: $MODE"
log "Architecture: arm64"
log "Version: $VERSION"
log "App: $APP_ABSOLUTE_PATH"
log "DMG: $DMG_ABSOLUTE_PATH"
log "DMG size: $DMG_SIZE bytes"

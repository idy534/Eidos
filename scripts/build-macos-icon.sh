#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_ICON="$ROOT_DIR/assets/branding/eidos-logo/svg/eidos-app-icon.svg"
OUTPUT_DIR="$ROOT_DIR/packaging"
OUTPUT_ICON="$OUTPUT_DIR/icon.icns"
STAGING_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/eidos-icon-build.XXXXXX")"
ICONSET_DIR="$STAGING_ROOT/Eidos.iconset"

cleanup() {
  rm -rf -- "$STAGING_ROOT"
}
trap cleanup EXIT

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "macOS is required to build packaging/icon.icns" >&2
  exit 1
fi
for command_name in iconutil sips; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "$command_name is required to build packaging/icon.icns" >&2
    exit 1
  fi
done
if [[ ! -f "$SOURCE_ICON" ]]; then
  echo "authoritative Eidos app icon source is missing: $SOURCE_ICON" >&2
  exit 1
fi

mkdir -p "$ICONSET_DIR" "$OUTPUT_DIR"

for size in 16 32 128 256 512; do
  sips -s format png -z "$size" "$size" "$SOURCE_ICON" \
    --out "$ICONSET_DIR/icon_${size}x${size}.png" >/dev/null
  doubled_size=$((size * 2))
  sips -s format png -z "$doubled_size" "$doubled_size" "$SOURCE_ICON" \
    --out "$ICONSET_DIR/icon_${size}x${size}@2x.png" >/dev/null
done

iconutil -c icns "$ICONSET_DIR" -o "$OUTPUT_ICON"
echo "Generated $OUTPUT_ICON from $SOURCE_ICON"

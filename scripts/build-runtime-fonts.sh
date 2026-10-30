#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="$ROOT_DIR/build/macos-runtime/dependencies/fonts"
CACHE="$ROOT_DIR/.cache/document-runtime"
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/eidos-font-build.XXXXXX")"
trap 'rm -rf -- "$STAGE"' EXIT
[[ ! -L "$ROOT_DIR/build" && ! -L "$ROOT_DIR/build/macos-runtime" && ! -L "$(dirname "$TARGET")" && ! -L "$TARGET" ]] || exit 1
mkdir -p "$CACHE" "$(dirname "$TARGET")" "$STAGE/fonts"
while read -r NAME URL CHECKSUM; do
  [[ "$NAME" != */* && "$CHECKSUM" =~ ^[0-9a-f]{64}$ ]] || exit 1
  CACHED="$CACHE/$NAME"
  if [[ ! -f "$CACHED" ]] || [[ "$(shasum -a 256 "$CACHED" | awk '{print $1}')" != "$CHECKSUM" ]]; then
    curl --fail --location --retry 3 --silent --show-error "$URL" --output "$STAGE/$NAME"
    [[ "$(shasum -a 256 "$STAGE/$NAME" | awk '{print $1}')" == "$CHECKSUM" ]] || exit 1
    mv "$STAGE/$NAME" "$CACHED"
  fi
  cp "$CACHED" "$STAGE/fonts/$NAME"
done < <(node -e 'for(const f of require(process.argv[1]).files) console.log(f.filename,f.url,f.sha256)' "$ROOT_DIR/resources/runtime-dependencies/fonts/files.json")
[[ -f "$STAGE/fonts/NotoSansSC.ttf" && -f "$STAGE/fonts/Carlito-Regular.ttf" ]] || exit 1
rm -rf -- "$TARGET"
mv "$STAGE/fonts" "$TARGET"
echo "Built bundled Chinese and Latin fonts at $TARGET"

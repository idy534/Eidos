#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_ROOT="$ROOT_DIR/resources/runtime-dependencies/node"
RELEASE_FILE="$SOURCE_ROOT/node-release.json"
BUILD_ROOT="$ROOT_DIR/build/macos-runtime"
DEFAULT_TARGET_ROOT="$BUILD_ROOT/dependencies/node"
STAGING_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/eidos-node-runtime-build.XXXXXX")"
TARGET_INPUT="${1:-$DEFAULT_TARGET_ROOT}"

cleanup() {
  rm -rf -- "$STAGING_ROOT"
}
trap cleanup EXIT

cd "$ROOT_DIR"

TARGET_ROOT="$(node --input-type=module - "$TARGET_INPUT" <<'NODE'
import path from "node:path";
console.log(path.resolve(process.argv[2]));
NODE
)"
node --input-type=module - "$TARGET_ROOT" "$BUILD_ROOT" "$STAGING_ROOT" <<'NODE'
import { existsSync, lstatSync, realpathSync } from "node:fs";
import path from "node:path";

const target = path.resolve(process.argv[2]);
const buildRoot = path.resolve(process.argv[3]);
const stagingRoot = path.resolve(process.argv[4]);
const isContained = (candidate, root) => {
  const relative = path.relative(root, candidate);
  return relative === "" || (!relative.startsWith(`..${path.sep}`) && relative !== ".." && !path.isAbsolute(relative));
};
if (target !== path.join(buildRoot, "dependencies", "node") && !isContained(target, stagingRoot)) {
  throw new Error("Node target must be build/macos-runtime/dependencies/node or a controlled staging path");
}
if (existsSync(target) && lstatSync(target).isSymbolicLink()) {
  throw new Error(`Node target must not be a symlink: ${target}`);
}
let current = target;
while (current !== path.parse(current).root) {
  if (existsSync(current)) {
    const metadata = lstatSync(current);
    if (!metadata.isSymbolicLink()) {
      const parent = path.dirname(current);
      if (current === target && !metadata.isDirectory()) {
        throw new Error(`Node target must be a directory: ${target}`);
      }
      current = parent;
      continue;
    }
    const resolved = realpathSync(current);
    if (!isContained(resolved, buildRoot) && !isContained(resolved, stagingRoot)) {
      throw new Error(`Node target contains an external symlink: ${current}`);
    }
  }
  const parent = path.dirname(current);
  if (parent === current) break;
  current = parent;
}
NODE

safe_remove_target() {
  case "$TARGET_ROOT" in
    "$BUILD_ROOT/dependencies/node"|"$STAGING_ROOT"/*)
      ;;
    *)
      echo "refusing to remove an uncontrolled Node target: $TARGET_ROOT" >&2
      exit 1
      ;;
  esac
  if [[ -L "$TARGET_ROOT" ]]; then
    echo "refusing to remove a symlinked Node target: $TARGET_ROOT" >&2
    exit 1
  fi
  rm -rf -- "$TARGET_ROOT"
}

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "macOS arm64 is required to build the bundled Node Runtime" >&2
  exit 1
fi
for command_name in curl rsync shasum tar node pnpm; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "$command_name is required to build the bundled Node Runtime" >&2
    exit 1
  fi
done
if [[ ! -f "$RELEASE_FILE" ]]; then
  echo "Node release pin is missing: $RELEASE_FILE" >&2
  exit 1
fi

read -r NODE_VERSION NODE_ARCHIVE NODE_URL NODE_CHECKSUMS_URL EXPECTED_SHA256 < <(
  node --input-type=module - "$RELEASE_FILE" <<'NODE'
import { readFileSync } from "node:fs";
const release = JSON.parse(readFileSync(process.argv[2], "utf8"));
if (release.target !== "darwin-arm64") throw new Error("Node release target must be darwin-arm64");
for (const key of ["version", "archive", "url", "checksumsUrl", "sha256"]) {
  if (typeof release[key] !== "string" || release[key].length === 0) {
    throw new Error(`Node release field is missing: ${key}`);
  }
}
console.log(release.version, release.archive, release.url, release.checksumsUrl, release.sha256);
NODE
)

ARCHIVE_PATH="$STAGING_ROOT/$NODE_ARCHIVE"
CHECKSUMS_PATH="$STAGING_ROOT/SHASUMS256.txt"
curl --fail --location --silent --show-error --retry 3 "$NODE_URL" --output "$ARCHIVE_PATH"
ACTUAL_ARCHIVE_SHA256="$(shasum -a 256 "$ARCHIVE_PATH" | awk '{print $1}')"
if [[ "$ACTUAL_ARCHIVE_SHA256" != "$EXPECTED_SHA256" ]]; then
  echo "Node archive SHA256 does not match the pinned release: $ACTUAL_ARCHIVE_SHA256" >&2
  exit 1
fi
curl --fail --location --silent --show-error --retry 3 "$NODE_CHECKSUMS_URL" --output "$CHECKSUMS_PATH"
OFFICIAL_SHA256="$(awk -v archive="$NODE_ARCHIVE" '$2 == archive { print $1; exit }' "$CHECKSUMS_PATH")"
if [[ "$OFFICIAL_SHA256" != "$EXPECTED_SHA256" ]]; then
  echo "Node archive SHA256 does not match the official SHASUMS256.txt entry" >&2
  exit 1
fi

tar -xJf "$ARCHIVE_PATH" -C "$STAGING_ROOT"
NODE_SOURCE_ROOT="$STAGING_ROOT/node-v$NODE_VERSION-darwin-arm64"
if [[ ! -x "$NODE_SOURCE_ROOT/bin/node" ]]; then
  echo "Node release archive does not contain bin/node" >&2
  exit 1
fi

safe_remove_target
mkdir -p "$TARGET_ROOT/bin" "$TARGET_ROOT/node_modules"
rsync -aL "$NODE_SOURCE_ROOT/bin/node" "$TARGET_ROOT/bin/node"
chmod 755 "$TARGET_ROOT/bin/node"
if [[ ! -f "$NODE_SOURCE_ROOT/LICENSE" ]]; then
  echo "Node release archive does not contain LICENSE" >&2
  exit 1
fi
rsync -a "$NODE_SOURCE_ROOT/LICENSE" "$TARGET_ROOT/LICENSE"

NODE_PROJECT_ROOT="$STAGING_ROOT/node-project"
mkdir -p "$NODE_PROJECT_ROOT"
rsync -a \
  "$SOURCE_ROOT/package.json" \
  "$SOURCE_ROOT/pnpm-lock.yaml" \
  "$NODE_PROJECT_ROOT/"
env -u NODE_OPTIONS pnpm --dir "$NODE_PROJECT_ROOT" install \
  --ignore-workspace \
  --frozen-lockfile \
  --ignore-scripts \
  --prod \
  --node-linker=hoisted \
  --reporter=append-only
rsync -aL --delete \
  --exclude='.pnpm/' \
  --exclude='.modules.yaml' \
  "$NODE_PROJECT_ROOT/node_modules/" \
  "$TARGET_ROOT/node_modules/"
rsync -a "$SOURCE_ROOT/runtime-loader.mjs" "$TARGET_ROOT/runtime-loader.mjs"
if ! cmp -s "$SOURCE_ROOT/runtime-loader.mjs" "$TARGET_ROOT/runtime-loader.mjs"; then
  echo "E-owned Node runtime loader changed while packaging" >&2
  exit 1
fi
if find "$TARGET_ROOT" -type l -print -quit | grep -q .; then
  echo "bundled Node Runtime contains a symlink; pnpm output was not flattened" >&2
  exit 1
fi
BUNDLED_NODE_VERSION="$(env -u NODE_OPTIONS "$TARGET_ROOT/bin/node" --version)"
if [[ "$BUNDLED_NODE_VERSION" != "v$NODE_VERSION" ]]; then
  echo "bundled Node executable reports an unexpected version" >&2
  exit 1
fi

echo "Built bundled Node $NODE_VERSION with isolated docx dependencies at $TARGET_ROOT"

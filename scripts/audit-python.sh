#!/bin/sh
set -eu

requirements_file=$(mktemp)
cache_dir=$(mktemp -d)
runtime_requirements_file=$(CDPATH= cd -- "$(dirname -- "$0")/../resources/runtime-dependencies/python" && pwd -P)/requirements.lock
if [ ! -f "$runtime_requirements_file" ]; then
  echo "missing isolated Runtime dependency lock: $runtime_requirements_file" >&2
  exit 1
fi
trap 'rm -f "$requirements_file"; rm -rf "$cache_dir"' EXIT HUP INT TERM

uv export --locked --no-dev --no-emit-project --format requirements-txt --output-file "$requirements_file" > /dev/null
uv run --locked pip-audit --cache-dir "$cache_dir" --disable-pip --requirement "$requirements_file"
uv run --locked pip-audit --cache-dir "$cache_dir" --disable-pip --requirement "$runtime_requirements_file"

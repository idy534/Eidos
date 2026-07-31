#!/bin/sh
set -eu

requirements_file=$(mktemp)
cache_dir=$(mktemp -d)
trap 'rm -f "$requirements_file"; rm -rf "$cache_dir"' EXIT HUP INT TERM

uv export --locked --no-dev --no-emit-project --format requirements-txt --output-file "$requirements_file" > /dev/null
uv run --locked pip-audit --cache-dir "$cache_dir" --disable-pip --requirement "$requirements_file"

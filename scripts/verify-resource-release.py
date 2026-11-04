#!/usr/bin/env python3
"""Check that every resource pack an App index points to is downloadable.

An App must not ship before its resource Release: a missing asset makes every
resource of that version fail with 404 on users' machines. HEAD requests follow
GitHub's redirect and compare the asset size; --full also checks SHA-256.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def check(pack: dict, full: bool) -> str | None:
    request = urllib.request.Request(pack["url"], method="GET" if full else "HEAD")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            if not full:
                size = response.headers.get("Content-Length")
                return None if size is None or int(size) == pack["archiveBytes"] else f"size {size} != {pack['archiveBytes']}"
            digest = hashlib.sha256()
            for chunk in iter(lambda: response.read(1 << 20), b""):
                digest.update(chunk)
            return None if digest.hexdigest() == pack["archiveSha256"] else "sha256 mismatch"
    except urllib.error.HTTPError as error:
        return f"HTTP {error.code}"
    except OSError as error:
        return f"unreachable ({error.__class__.__name__})"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("index", nargs="?", type=Path, default=ROOT / "build/macos-runtime/resource-index.json")
    parser.add_argument("--full", action="store_true", help="download every asset and compare SHA-256")
    args = parser.parse_args()
    index = json.loads(args.index.read_text())
    failures = [(pack["id"], problem) for pack in index["packs"] if (problem := check(pack, args.full))]
    for pack_id, problem in failures:
        print(f"missing resource {pack_id}: {problem}", file=sys.stderr)
    if failures:
        print(f"Resource Release {index['version']} is not ready; publish it before releasing the App "
              "(python3 scripts/publish-resource-packs.py --publish).", file=sys.stderr)
        return 1
    print(f"All {len(index['packs'])} resource packs of {index['version']} are downloadable.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

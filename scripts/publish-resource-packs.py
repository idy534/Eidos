#!/usr/bin/env python3
"""Verify fixed resource assets; publish to the configured release with --publish."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=ROOT / "build/resource-packages")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    release = json.loads((ROOT / "resources/runtime-dependencies/resource-release.json").read_text())
    directory = args.directory.resolve()
    index_path = directory / "resource-index.json"
    index = json.loads(index_path.read_text())
    if index["version"] != release["version"]:
        raise ValueError("resource release version does not match the build")
    assets, rows = [index_path], []
    for pack in index["packs"]:
        url = urlsplit(pack["url"])
        prefix = f"https://github.com/{release['repository']}/releases/download/{release['tag']}/"
        if not pack["url"].startswith(prefix) or url.query or url.fragment:
            raise ValueError("resource asset URL does not match the configured release")
        file = directory / Path(url.path).name
        with file.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if file.stat().st_size != pack["archiveBytes"] or digest != pack["archiveSha256"]:
            raise ValueError(f"resource archive changed: {file.name}")
        assets.append(file)
        rows.append(f"| {pack['id']} | {pack['archiveBytes'] / 1024**2:.2f} MiB | {pack['expandedBytes'] / 1024**2:.2f} MiB |")
    notes = directory / "release-notes.md"
    notes.write_text(
        f"Fixed document resources for Eidos. Version {release['version']}; macOS arm64 / CPython 3.12.\n\n"
        "Eidos downloads these resources only for the selected document operation. "
        "The App contains the pinned index and verifies the archive and every extracted file. "
        "Third-party notices are retained inside the archives.\n\n"
        "| Resource | Download | Installed |\n| --- | ---: | ---: |\n" + "\n".join(rows) + "\n"
    )
    print(f"Verified {len(assets) - 1} packs: {sum(p.stat().st_size for p in assets) / 1024**2:.2f} MiB")
    print(f"Release: https://github.com/{release['repository']}/releases/tag/{release['tag']}")
    if not args.publish:
        print("Verification only. Pass --publish to create the public resource release.")
        return
    # No clobber/update path: a published version is immutable. This resource
    # release must never become the application's latest update release.
    subprocess.run([
        "gh", "release", "create", release["tag"], "--repo", release["repository"],
        "--title", f"Document resources {release['version']}", "--notes-file", str(notes),
        "--latest=false", *map(str, assets),
    ], check=True)


if __name__ == "__main__":
    main()

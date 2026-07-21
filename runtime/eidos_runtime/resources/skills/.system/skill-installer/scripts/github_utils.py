#!/usr/bin/env python3
"""Shared GitHub helpers for skill install scripts."""

from __future__ import annotations

import os
from pathlib import PurePosixPath
import re
import urllib.parse
import urllib.request


REPOSITORY_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")


def github_request(url: str, user_agent: str, limit: int = 4 * 1024 * 1024) -> bytes:
    headers = {"User-Agent": user_agent}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"token {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = resp.read(limit + 1)
    if len(payload) > limit:
        raise ValueError("GitHub response is too large")
    return payload


def github_api_contents_url(repo: str, path: str, ref: str) -> str:
    parts = repo.split("/")
    pure = PurePosixPath(path)
    if (
        len(parts) != 2
        or not all(REPOSITORY_PART.fullmatch(part) for part in parts)
        or pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
        or not ref
        or any(character in ref for character in "\x00\r\n")
    ):
        raise ValueError("Invalid GitHub repository, path, or ref")
    owner, name = (urllib.parse.quote(part, safe="") for part in parts)
    encoded_path = "/".join(urllib.parse.quote(part, safe="") for part in pure.parts)
    return (
        f"https://api.github.com/repos/{owner}/{name}/contents/{encoded_path}"
        f"?ref={urllib.parse.quote(ref, safe='')}"
    )

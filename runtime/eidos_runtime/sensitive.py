from __future__ import annotations

import json
from functools import lru_cache
import os
from pathlib import Path
import re
import stat
import time
from typing import Literal

from pydantic import Field

from eidos_runtime.schemas import ClosedModel


MAX_SCAN_BYTES = 512 * 1024
SCAN_TIMEOUT_SECONDS = 1.0


class SensitiveScanError(RuntimeError):
    pass


class SensitiveContentDenied(SensitiveScanError):
    def __init__(self, rule_id: str) -> None:
        super().__init__("sensitive content denied")
        self.rule_id = rule_id


class SensitiveRule(ClosedModel):
    id: str
    version: int
    priority: int
    action: Literal["deny", "redact", "allow_with_audit"]
    pattern: str


class SensitiveRuleSet(ClosedModel):
    version: Literal[1]
    rules: list[SensitiveRule]


class ScanResult(ClosedModel):
    text: str
    audited_rule_ids: list[str] = Field(default_factory=list, alias="auditedRuleIds")


class SensitiveScanner:
    def __init__(self, rules_path: Path | None = None) -> None:
        path = rules_path or Path(__file__).with_name("sensitive_rules.json")
        try:
            metadata = path.lstat()
            if (
                path.is_symlink()
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) & 0o022
            ):
                raise ValueError("rules resource is not read-only")
            rules = SensitiveRuleSet.model_validate_json(path.read_text(encoding="utf-8"))
            ordered = sorted(rules.rules, key=lambda rule: (rule.priority, rule.id))
            if (
                len({rule.id for rule in ordered}) != len(ordered)
                or any(rule.version < 1 or rule.priority < 0 for rule in ordered)
            ):
                raise ValueError("duplicate rule id")
            self.rules = tuple((rule, re.compile(rule.pattern)) for rule in ordered)
            self.version = rules.version
        except Exception as error:
            raise SensitiveScanError("sensitive rules are invalid") from error

    def scan_text(self, value: str) -> ScanResult:
        if not isinstance(value, str):
            raise SensitiveScanError("text is invalid")
        encoded = value.encode("utf-8", errors="strict")
        if len(encoded) > MAX_SCAN_BYTES:
            raise SensitiveScanError("sensitive scan capacity exceeded")
        deadline = time.monotonic() + SCAN_TIMEOUT_SECONDS
        safe = value
        audited: list[str] = []
        for rule, pattern in self.rules:
            if time.monotonic() > deadline:
                raise SensitiveScanError("sensitive scan timed out")
            match = pattern.search(safe)
            if match is None:
                continue
            if rule.action == "deny":
                raise SensitiveContentDenied(rule.id)
            if rule.action == "redact":
                safe = pattern.sub(f"[REDACTED:{rule.id}]", safe)
            else:
                audited.append(rule.id)
        return ScanResult(text=safe, auditedRuleIds=audited)

    def scan_json(self, value: object) -> object:
        if isinstance(value, str):
            return self.scan_text(value).text
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, list):
            return [self.scan_json(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.scan_json(item) for item in value)
        if isinstance(value, dict) and all(isinstance(key, str) for key in value):
            return {key: self.scan_json(item) for key, item in value.items()}
        raise SensitiveScanError("JSON value is invalid")


class StreamingSensitiveScanner:
    def __init__(self, scanner: SensitiveScanner) -> None:
        self.scanner = scanner
        self.parts: list[str] = []
        self.bytes = 0

    def feed(self, chunk: str) -> None:
        try:
            size = len(chunk.encode("utf-8", errors="strict"))
        except UnicodeError as error:
            raise SensitiveScanError("stream encoding is invalid") from error
        self.bytes += size
        if self.bytes > MAX_SCAN_BYTES:
            raise SensitiveScanError("sensitive scan capacity exceeded")
        self.parts.append(chunk)

    def finish(self) -> ScanResult:
        return self.scanner.scan_text("".join(self.parts))


@lru_cache(maxsize=1)
def default_scanner() -> SensitiveScanner:
    return SensitiveScanner()

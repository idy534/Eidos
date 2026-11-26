"""Read the bounded completion record from the bundled MXC launcher."""
from __future__ import annotations

from dataclasses import dataclass
import json


MAX_MXC_STATUS_BYTES = 1024
_PRELAUNCH_CODES = frozenset({
    "request_invalid", "backend_unavailable", "policy_rejected", "launch_failed",
})


@dataclass(frozen=True)
class MxcProcessStatus:
    exit_code: int | None = None
    failure_code: str | None = None

    @property
    def uncertain(self) -> bool:
        return self.exit_code is None and self.failure_code is None


def parse_mxc_status(payload: bytes, launcher_exit_code: int) -> MxcProcessStatus:
    """Interpret a closed status pipe after the launcher has exited."""
    if len(payload) > MAX_MXC_STATUS_BYTES or not payload.endswith(b"\n"):
        return MxcProcessStatus()
    lines = payload[:-1].split(b"\n")
    if len(lines) not in {1, 2}:
        return MxcProcessStatus()
    try:
        records = [json.loads(line.decode("utf-8"), object_pairs_hook=_unique_object) for line in lines]
    except (ValueError, RecursionError):
        return MxcProcessStatus()

    final = records[-1]
    if not isinstance(final, dict):
        return MxcProcessStatus()
    if len(records) == 1 and final.keys() == {"event", "code"} and final["event"] == "failed":
        code = final["code"]
        if launcher_exit_code != 0 and isinstance(code, str) and code in _PRELAUNCH_CODES:
            return MxcProcessStatus(failure_code=code)
    if len(records) == 2 and records[0] == {"event": "started"} and final.keys() == {"event", "exitCode"}:
        code = final["exitCode"]
        # The SDK sends an unsigned DWORD. A signed host return code has the same bits.
        if final["event"] == "exited" and type(code) is int and code == (launcher_exit_code & 0xFFFF_FFFF):
            return MxcProcessStatus(exit_code=code)
    return MxcProcessStatus()


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("duplicate MXC status field")
    return value

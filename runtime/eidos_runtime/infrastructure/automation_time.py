from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
import tzdata

from eidos_runtime.domain.automation import AutomationError, Schedule

TIMEZONE_DATA_VERSION = tzdata.__version__


def local_timezone() -> str:
    candidates = [os.environ.get("TZ", "")]
    resolved = str(Path("/etc/localtime").resolve())
    if "zoneinfo/" in resolved:
        candidates.append(resolved.split("zoneinfo/", 1)[1])
    for candidate in candidates:
        try:
            if candidate:
                ZoneInfo(candidate)
                return candidate
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return "UTC"


def normalize(kind: str, schedule: Schedule, *, default_timezone: str | None = None) -> Schedule:
    name = schedule.timezone or default_timezone or local_timezone()
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise AutomationError("automation_schedule_not_supported", "时区无效，请使用地区时区名称。") from None
    if kind == "at" and schedule.run_at is not None:
        try:
            value = datetime.fromisoformat(schedule.run_at.replace("Z", "+00:00"))
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError
            return Schedule(run_at=value.astimezone(timezone.utc).isoformat(), timezone=name)
        except (ValueError, OverflowError):
            raise AutomationError("automation_schedule_not_supported", "一次性时间需要明确日期和时区偏移。") from None
    if kind != "cron" or schedule.expression is None or not schedule.expression.isascii():
        raise AutomationError("automation_schedule_not_supported", "时间类型与规则不匹配。")
    expression = " ".join(schedule.expression.split())
    parts = expression.split()
    try:
        expanded, _ = croniter.expand(expression)
        if len(parts) != 5 or len(expanded) != 5 or parts[3] != "*":
            raise ValueError
        if expression not in {"*/10 * * * *", "*/30 * * * *", "0 * * * *"}:
            # The parser owns Cron syntax. Eidos only accepts one daily time,
            # an unrestricted month, and either weekdays or one month-day.
            if not parts[0].isdigit() or not parts[1].isdigit():
                raise ValueError
            if len(expanded[0]) != 1 or len(expanded[1]) != 1:
                raise ValueError
            if parts[2] != "*" and (not parts[2].isdigit() or parts[4] != "*"):
                raise ValueError
            if parts[4] != "*" and (parts[2] != "*" or any(char not in "0123456,-" for char in parts[4])):
                raise ValueError
    except (ValueError, KeyError, TypeError):
        raise AutomationError("automation_schedule_not_supported", "固定间隔只支持 10 分钟、30 分钟、1 小时；日历任务支持每天、每周或每月的一个时间。") from None
    return Schedule(expression=expression, timezone=name)


def future_times(kind: str, schedule: Schedule, now: datetime, count: int = 5) -> list[str]:
    schedule = normalize(kind, schedule)
    if now.tzinfo is None:
        raise ValueError("clock must be timezone-aware")
    if kind == "at":
        value = datetime.fromisoformat(schedule.run_at or "")
        return [value.isoformat()] if value > now else []
    iterator = croniter(schedule.expression, now.astimezone(ZoneInfo(schedule.timezone or "UTC")), max_years_between_matches=5)
    expanded, _ = croniter.expand(schedule.expression)
    values: list[str] = []
    wanted = min(max(count, 1), 5)
    for _ in range(wanted + 8):
        value = iterator.get_next(datetime).astimezone(timezone.utc)
        local = value.astimezone(ZoneInfo(schedule.timezone or "UTC"))
        # Skip nonexistent local wall times rather than running at another hour.
        if (value > now and value.isoformat() not in values
            and ("*" in expanded[0] or local.minute in expanded[0])
            and ("*" in expanded[1] or local.hour in expanded[1])
            and croniter.match(schedule.expression, local)):
            values.append(value.isoformat())
            if len(values) == wanted:
                break
    return values

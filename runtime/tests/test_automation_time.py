from datetime import datetime, timezone
from pathlib import Path

import pytest

from eidos_runtime.domain.automation import AutomationError, Schedule
from eidos_runtime.infrastructure.automation_time import future_times, local_timezone, normalize


@pytest.mark.parametrize(("configured", "expected", "next_run"), [
    ("Asia/Shanghai", "Asia/Shanghai", "2026-10-26T01:00:00+00:00"),
    (":Asia/Shanghai", "Asia/Shanghai", "2026-10-26T01:00:00+00:00"),
    ("CST-8", "Etc/GMT-8", "2026-10-26T01:00:00+00:00"),
    ("UTC0", "UTC", "2026-10-26T09:00:00+00:00"),
    ("GMT+3", "Etc/GMT+3", "2026-10-26T12:00:00+00:00"),
    ("", "UTC", "2026-10-26T09:00:00+00:00"),
])
def test_default_timezone_preserves_supported_environment_meaning(monkeypatch, configured, expected, next_run):
    monkeypatch.setenv("TZ", configured)
    # The machine zone must not override an explicit process zone.
    monkeypatch.setattr(Path, "resolve", lambda self: Path("/var/db/timezone/zoneinfo/Asia/Seoul"))
    schedule = normalize("cron", Schedule(expression="0 9 * * *"))
    assert schedule.timezone == expected
    assert future_times("cron", schedule, datetime(2026, 10, 26, tzinfo=timezone.utc), 1) == [next_run]


@pytest.mark.parametrize("configured", ["invalid-zone", "CST-25", "EST5EDT,M3.2.0,M11.1.0", "CST-8:30"])
def test_unsupported_environment_zone_requires_an_explicit_region(monkeypatch, configured):
    monkeypatch.setenv("TZ", configured)
    with pytest.raises(AutomationError, match="TZ 环境变量不受支持"):
        local_timezone()
    # Explicit plans continue to work even with an unsupported host TZ.
    schedule = normalize("cron", Schedule(expression="0 9 * * *", timezone="Asia/Shanghai"))
    assert schedule.timezone == "Asia/Shanghai"


def test_system_timezone_change_only_affects_new_default_schedules(monkeypatch):
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setattr(Path, "resolve", lambda self: Path("/var/db/timezone/zoneinfo/Asia/Shanghai"))
    old = normalize("cron", Schedule(expression="0 9 * * *"))
    monkeypatch.setattr(Path, "resolve", lambda self: Path("/var/db/timezone/zoneinfo/Asia/Seoul"))
    new = normalize("cron", Schedule(expression="0 9 * * *"))
    assert new.timezone == "Asia/Seoul"
    assert normalize("cron", old).timezone == "Asia/Shanghai"

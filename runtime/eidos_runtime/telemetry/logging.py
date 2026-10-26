"""Unambiguous UTC timestamps for both Runtime stderr entry points."""

from __future__ import annotations

import logging
import sys
import time


class UtcFormatter(logging.Formatter):
    converter = time.gmtime
    default_time_format = "%Y-%m-%dT%H:%M:%S"
    default_msec_format = "%s.%03dZ"


def configure_stderr_logging() -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(UtcFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])

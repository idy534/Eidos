#!/usr/bin/env python3
from pathlib import Path


root = Path(__file__).resolve().parents[1]
print((root / "references" / "format-guide.md").read_text(encoding="utf-8"))

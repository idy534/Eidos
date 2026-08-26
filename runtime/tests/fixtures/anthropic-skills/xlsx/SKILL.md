---
name: xlsx
description: |
  Create, edit, and inspect spreadsheets: preserve formulas and formatting,
  review tables, and use existing local tools.
license: Complete terms in LICENSE
compatibility: local: existing Eidos tools only
metadata:
  short-description: Use existing local tools.
  author: Anthropic
  format: xlsx
allowed-tools:
  - read_file
  - run_shell
  - view_image
argument-hint: Use a workbook path: e.g. assets/template.xlsx
---

# XLSX skill

Use the existing `read_file`, `run_shell`, and `view_image` tools. Do not
install Python, npm, or system packages. Read `references/format-guide.md`
when a workbook needs format-specific handling. Run `python3 scripts/render.py`
from this skill directory when the task needs the local rendering helper.

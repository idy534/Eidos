---
name: pdf
description: |
  Create, edit, and inspect PDF documents: preserve page structure,
  review rendered pages, and use existing local tools.
license: Complete terms in LICENSE
compatibility: local: existing Eidos tools only
metadata:
  short-description: Use existing local tools.
  author: Anthropic
  format: pdf
allowed-tools:
  - read_file
  - run_shell
  - view_image
argument-hint: Use a PDF path: e.g. assets/template.pdf
---

# PDF skill

Use the existing `read_file`, `run_shell`, and `view_image` tools. Do not
install Python, npm, or system packages. Read `references/format-guide.md`
when a PDF needs format-specific handling. Run `python3 scripts/render.py`
from this skill directory when the task needs the local rendering helper.

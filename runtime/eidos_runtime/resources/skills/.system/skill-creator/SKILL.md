---
name: skill-creator
description: Create or update concise Eidos skills with strict SKILL.md metadata and optional scripts or references. Use when a user asks to create, revise, or validate a user skill.
---

# Skill Creator

Create user skills in `${EIDOS_DATA_DIR:-$HOME/.eidos}/skills/<skill-name>`. The `.system` directory is managed by Eidos and must not be edited through this skill.

Eidos v0.3 does not allow Agent Shell to write `~/.eidos`. Give the user the exact helper command to run in the system Terminal, then validate the resulting files. Project-local skill drafts may still be created with normal file tools when the user explicitly chooses a project path.

## Contract

Each skill has one required `SKILL.md`:

```text
skill-name/
├── SKILL.md
├── scripts/      optional deterministic helpers
└── references/   optional detailed UTF-8 context
```

`SKILL.md` frontmatter contains exactly `name` and `description`. The folder and `name` must match lowercase hyphen-case and be at most 64 characters. Put triggering conditions in `description`; keep the body procedural and under 500 lines. Eidos reads UTF-8 scripts and references as text resources but does not execute them automatically. Binary assets are not a supported user-skill resource in Eidos v0.3.

## Create

Run from this skill directory:

```bash
python3 scripts/init_skill.py <skill-name>
python3 scripts/init_skill.py <skill-name> --resources scripts,references
python3 scripts/init_skill.py <skill-name> --path /explicit/parent
```

Edit the generated `SKILL.md`, remove every TODO, and create only resources required by repeated work. Do not add README, changelog, installation guide, or speculative scaffolding.

## Validate

```bash
python3 scripts/quick_validate.py <path/to/skill>
```

Run relevant bundled scripts directly before declaring the skill usable. A newly installed user skill is discovered by a new Run; an existing Run keeps its frozen skill snapshot.

Do not add `agents/openai.yaml` to user skills. The current Eidos catalog does not consume it.

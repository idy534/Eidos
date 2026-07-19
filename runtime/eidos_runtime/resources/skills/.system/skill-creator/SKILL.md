---
name: skill-creator
description: Create a new concise Eidos user skill with strict SKILL.md metadata. Use when a user asks to create a skill for repeated work.
---

# Skill Creator

Create user skills with the built-in `skill_create` tool. Eidos stores them in `${EIDOS_DATA_DIR:-$HOME/.eidos}/skills/<skill-name>`. The `.system` directory is managed by Eidos and must never be edited or shadowed.

Do not use Agent Shell or workspace file tools for this operation. Draft the three tool arguments, call `skill_create` once, and let Eidos show the exact `SKILL.md` change for user approval. Rejection must be treated as final and leaves no skill behind.

## Contract

The current creation tool creates one required `SKILL.md`:

```text
skill-name/
└── SKILL.md
```

`SKILL.md` frontmatter contains exactly `name` and `description`. The name must start with a lowercase letter, contain only lowercase letters, digits, and hyphens, and be at most 64 characters. The description is one non-empty line describing when to use the skill. Keep instructions procedural, concise, free of secrets, and below the tool size limit.

## Create

Call `skill_create` with exactly:

- `name`: validated lowercase hyphen-case name.
- `description`: one-line trigger description.
- `instructions`: complete `SKILL.md` body without frontmatter.

Eidos generates the frontmatter and heading, rejects collisions, and writes only after approval. Do not ask the user to run Terminal commands for ordinary skill creation. A newly created skill is available to a new Run as `user:<name>`; the current Run retains its frozen skill snapshot.

## Validate

Before calling the tool, verify the name and description constraints, ensure instructions contain no placeholder TODOs, and avoid README, changelog, installation guide, `agents/openai.yaml`, or speculative scaffolding. The current tool does not update an existing skill or create optional resource files.

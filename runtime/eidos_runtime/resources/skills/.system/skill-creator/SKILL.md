---
name: skill-creator
description: Create a concise Eidos user skill with optional scripts, references, assets, and other text resources. Use when a user asks to create a skill for repeated work.
---

# Skill Creator

Create user skills with the built-in `skill_create` tool. Eidos stores them in `${EIDOS_DATA_DIR:-$HOME/.eidos}/skills/<skill-name>`. The `.system` directory is managed by Eidos and must never be edited or shadowed.

Do not use Agent Shell or workspace file tools for this operation. Draft the tool arguments, call `skill_create` once, and let Eidos show the exact skill tree for user approval. Rejection must be treated as final and leaves no skill behind.

## Contract

The creation tool always creates `SKILL.md` and may include text resources:

```text
skill-name/
├── SKILL.md
├── scripts/       # optional deterministic helpers
├── references/    # optional detailed context
└── assets/        # optional text templates
```

`SKILL.md` frontmatter contains exactly `name` and `description`. The name must start with a lowercase letter, contain only lowercase letters, digits, and hyphens, and be at most 64 characters. The description is one non-empty line describing when to use the skill. Keep instructions procedural, concise, free of secrets, and below the tool size limit.

## Create

Call `skill_create` with:

- `name`: validated lowercase hyphen-case name.
- `description`: one-line trigger description.
- `instructions`: complete `SKILL.md` body without frontmatter.
- `files` (optional): a list of `{path, content}` UTF-8 resource files relative to the skill root. Never provide `SKILL.md` here.

Eidos generates the frontmatter and heading, rejects collisions and unsafe paths, and writes the complete tree atomically only after approval. Use bundled files only when they materially improve reliability or progressive disclosure. Do not create README, changelog, installation guide, or speculative scaffolding. A newly created skill is available to a new Run as `user:<name>`; the current Run retains its frozen skill snapshot.

## Validate

Before calling the tool, verify the name and description constraints, ensure instructions and resources contain no placeholder TODOs or secrets, and keep every resource inside the skill root. The current tool does not update an existing skill or create binary resources.

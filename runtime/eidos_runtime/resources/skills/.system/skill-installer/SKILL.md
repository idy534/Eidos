---
name: skill-installer
description: Install a complete Eidos user skill from an exact public GitHub tree URL. Use when a user asks to install a GitHub skill into the current Eidos Home.
---

# Skill Installer

Install user skills into `${EIDOS_DATA_DIR:-$HOME/.eidos}/skills/<skill-name>`. Never write to the managed `.system` directory.

Use the built-in `skill_install` tool. Do not use Agent Shell, `skill_create`, manual file writes, `git`, `curl`, or Python download snippets for installation.

## Install

Call `skill_install` once with the exact HTTPS GitHub tree URL supplied by the user:

```json
{
  "url": "https://github.com/<owner>/<repo>/tree/<ref>/<path-to-skill>"
}
```

Eidos first asks for one-time network approval limited to `codeload.github.com:443`. After downloading and validating the full package, Eidos shows the target skill tree for a separate Eidos State write approval. Treat either rejection as final.

The Runtime validates the GitHub URL, archive paths, symlinks, special files, size limits, `SKILL.md` metadata, destination collision, ownership, private staging, and atomic rename. The complete skill tree is preserved, including valid `scripts/`, `references/`, `assets/`, and other bundled resources.

## Limits

- Install one public GitHub skill directory per call.
- Require an exact `/tree/<ref>/<path>` URL.
- Do not install or overwrite `.system` skills.
- Do not replace an existing user skill.
- Private repositories and curated remote listing are not supported by this tool version.

After success, tell the user the skill is available to a newly started Run. The current Run keeps its frozen Skill snapshot.

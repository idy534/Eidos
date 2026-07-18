---
name: skill-installer
description: Install Eidos user skills from the curated openai/skills repository or another GitHub repository. Use when a user asks to list available skills or install a skill into the current Eidos Home.
---

# Skill Installer

Install user skills into `${EIDOS_DATA_DIR:-$HOME/.eidos}/skills/<skill-name>`. Never write to the managed `.system` directory.

Eidos v0.3 does not allow Agent Shell to access `~/.eidos` or the network. Give the user the exact helper command to run in the system Terminal; do not claim that `run_shell` installed the skill.

## List skills

Run from this skill directory:

```bash
python3 scripts/list-skills.py
python3 scripts/list-skills.py --format json
python3 scripts/list-skills.py --path skills/.experimental
```

Label the repository/path and mark skills already present in either the user or system catalog.

## Install skills

```bash
python3 scripts/install-skill-from-github.py \
  --repo openai/skills \
  --path skills/.curated/<skill-name>

python3 scripts/install-skill-from-github.py \
  --url https://github.com/<owner>/<repo>/tree/<ref>/<path>
```

For private repositories, the script uses existing Git credentials or `GITHUB_TOKEN`/`GH_TOKEN`. It rejects an existing destination, unsafe paths, symlinks, special files, invalid metadata, and oversized skills. It installs with private permissions and an atomic final rename.

After installation, tell the user the skill becomes available to a newly started Run. Do not install `.system` skills: they ship with Eidos.

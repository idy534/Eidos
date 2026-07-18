---
name: plugin-creator
description: Create or validate a local Eidos Plugin v1 containing Skill declarations and optional stdio MCP server configuration. Use when a user asks to scaffold or update an Eidos plugin directory.
---

# Plugin Creator

Create the smallest Eidos Plugin v1. Eidos plugins are local directories with `plugin.json` at the root. There is no marketplace, `.codex-plugin`, hook, app, installer, remote download, or update contract.

Create the plugin inside the active Workspace so normal Eidos file and Shell boundaries apply. Importing it remains a separate explicit Desktop action.

Run from this skill directory:

```bash
python3 scripts/create_basic_plugin.py <plugin-id> --path <parent-directory>
python3 scripts/create_basic_plugin.py <plugin-id> --path <parent-directory> --skill <skill-name>
python3 scripts/validate_plugin.py <plugin-directory>
```

The closed manifest accepts exactly:

- `schemaVersion`: `1`
- `id`, `name`, `version`, `description`
- `skills`: objects containing only relative `root`
- `mcpServers`: structured stdio server declarations

MCP declarations use `id`, `executable`, `argv`, `envNames`, `permissionProfile`, `startupTimeoutSeconds`, `toolTimeoutSeconds`, and `enabled`. Never encode a shell command, install hook, dependency resolver, token value, or environment value.

Validate before handing back the plugin. Import remains an explicit Desktop action and does not execute plugin files.

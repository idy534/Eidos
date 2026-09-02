---
name: plugin-creator
description: Create or validate a local Eidos Plugin v1 containing Skill declarations and optional stdio MCP server configuration. Use when a user asks to scaffold or update an Eidos plugin directory.
---

# Plugin Creator

Create the smallest Eidos Plugin v1. Eidos plugins are local directories with `plugin.json` at the root. There is no marketplace, `.codex-plugin`, hook, app, installer, remote download, or update contract.

Create the plugin inside the active Workspace so normal Eidos file and Shell boundaries apply. Importing it remains a separate explicit Desktop action.

The bundled helper scripts use only Python standard-library modules. Use the Eidos-owned runtime binding for every helper invocation. Do not use a host `python3`, `python`, `uv`, `pip`, `npm`, or `node` command.

First call `workspace_dependencies` with `{}`. Select the active binding whose `skillQualifiedId` is `system:plugin-creator` and whose `status` is `"ready"` from `data.activeSkillDependencyBindings` in the result. If that matching ready entry is absent, stop and report its bounded dependency diagnostic. Never treat an invalid or non-ready declaration as no dependency. Never substitute the top-level `data.defaultDependencyBindingId` for this Skill. Save that record's `bindingId` as `dependencyBindingId` and pass it to every `run_shell` call. Keep `cwd` set to the active Workspace (`.`) for every call. The Skill directory is read-only, so the command must use the canonical absolute Skill root supplied by the Skill catalog and write generated plugins only below the active Workspace.

Use one JSON object per `run_shell` call. The commands have these shapes:

Create a plugin:
```json
{
  "dependencyBindingId": "<dependencyBindingId from workspace_dependencies>",
  "command": "\"$RUNTIME_PYTHON\" \"<absolute skill root>/scripts/create_basic_plugin.py\" <plugin-id> --path <workspace-relative-parent>",
  "cwd": "."
}
```

Create a plugin with a Skill:

```json
{
  "dependencyBindingId": "<dependencyBindingId from workspace_dependencies>",
  "command": "\"$RUNTIME_PYTHON\" \"<absolute skill root>/scripts/create_basic_plugin.py\" <plugin-id> --path <workspace-relative-parent> --skill <skill-name>",
  "cwd": "."
}
```

Validate a plugin:

```json
{
  "dependencyBindingId": "<dependencyBindingId from workspace_dependencies>",
  "command": "\"$RUNTIME_PYTHON\" \"<absolute skill root>/scripts/validate_plugin.py\" <workspace-relative-plugin-directory>",
  "cwd": "."
}
```

Replace `<absolute skill root>` with the real canonical absolute root returned by the Skill catalog before sending the command. Never pass that placeholder literally. The helper scripts may create or validate a plugin below the active Workspace. Never write the Skill root or any other bundled Skill resource. Do not install packages or modify the host environment.

The closed manifest accepts exactly:

- `schemaVersion`: `1`
- `id`, `name`, `version`, `description`
- `skills`: objects containing only relative `root`
- `mcpServers`: structured stdio server declarations

MCP declarations use `id`, `executable`, `argv`, `envNames`, `permissionProfile`, `startupTimeoutSeconds`, `toolTimeoutSeconds`, and `enabled`. Never encode a shell command, install hook, dependency resolver, token value, or environment value.

Validate before handing back the plugin. Import remains an explicit Desktop action and does not execute plugin files.

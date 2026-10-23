---
name: plugin-creator
description: Create or preflight an Eidos plugin with Skills, optional command Hooks, and local stdio or remote Streamable HTTP MCP. Use when a user asks to scaffold or update an Eidos plugin directory. Runtime import performs full validation.
---

# Plugin Creator

Create a small Eidos plugin. The scaffold uses Eidos Plugin v1 with root `plugin.json`. Eidos also imports portable Agent Plugins and `.codex-plugin/plugin.json` compatibility packages. Plugins can provide Skills, supported command Hooks, local stdio MCP and remote Streamable HTTP MCP. A later version can be imported to update a plugin; Eidos preserves files referenced by active Runs and requires fresh enablement, Hook trust, MCP consent and remote account connection. Remote authentication supports user-entered bearer tokens, public-client OAuth through the MCP SDK, and the built-in GitHub Device Flow. Codex App connection mappings are not portable to Eidos.

Create the plugin inside the active Workspace so normal Eidos file and Shell boundaries apply. Importing it remains a separate explicit Desktop action.

The bundled helper scripts use only Python standard-library modules. Eidos automatically selects this Skill's verified runtime when a command directly invokes one of its scripts as shown below. Do not supply or cache a dependency binding ID. Use "$RUNTIME_PYTHON" rather than a host interpreter. If the declared runtime is unavailable, report the dependency error instead of bypassing it. Keep `cwd` set to the active Workspace (`.`) for every call. The Skill directory is read-only, so the command must use the canonical absolute Skill root supplied by the Skill catalog and write generated plugins only below the active Workspace.

Use one JSON object per `run_shell` call. The commands have these shapes:

Create a plugin:
```json
{
  "command": "\"$RUNTIME_PYTHON\" \"<absolute skill root>/scripts/create_basic_plugin.py\" <plugin-id> --path <workspace-relative-parent>",
  "cwd": "."
}
```

Create a plugin with a Skill:

```json
{
  "command": "\"$RUNTIME_PYTHON\" \"<absolute skill root>/scripts/create_basic_plugin.py\" <plugin-id> --path <workspace-relative-parent> --skill <skill-name>",
  "cwd": "."
}
```

Run package preflight:

```json
{
  "command": "\"$RUNTIME_PYTHON\" \"<absolute skill root>/scripts/validate_plugin.py\" <workspace-relative-plugin-directory>",
  "cwd": "."
}
```

Replace `<absolute skill root>` with the real canonical absolute root returned by the Skill catalog before sending the command. Never pass that placeholder literally. The helper scripts may create or validate a plugin below the active Workspace. Never write the Skill root or any other bundled Skill resource. Do not install packages or modify the host environment.

The scaffold's manifest uses:

- `schemaVersion`: `1`
- `id`, `name`, `version`, `description`
- `skills`: objects containing only relative `root`
- `mcpServers`: structured local or remote server declarations

Optional package fields include `hooks`, `interface`, `author`, `homepage`, `license` and `onboardingSkill`. The preflight checks file safety, capacity and basic format. It deliberately does not implement another YAML or manifest parser. Runtime import performs the authoritative declaration and Skill metadata validation, including rich YAML and optional metadata.

MCP declarations use `id`, `executable`, `argv`, `envNames`, `permissionProfile`, `startupTimeoutSeconds`, `toolTimeoutSeconds`, and `enabled`. Never encode a shell command, install hook, dependency resolver, token value, or environment value.

For a remote Eidos v1 server, set `transport: "streamable-http"`, `url`, `permissionProfile: "connector"`, `enabled`, and optional `auth`. Omit executable, argv and environment bindings. Portable packages use `type: "streamable-http"` in `mcp.json` and put Eidos authentication under `extensions.com.eidos.auth`. See the reference for an example. Use HTTPS; only explicit loopback URLs may use HTTP. Never put tokens, client secrets, static Authorization headers, cookies or credentials in URL parameters. The user connects the account in plugin settings. A public Client ID may be packaged only when its registration supports the Eidos callback or Device Flow. Do not reuse another product's client identity.

Run preflight before handing back the plugin and describe its scope accurately. Import remains an explicit Desktop action and does not execute plugin files. Enabling a plugin does not grant MCP consent or Hook trust.

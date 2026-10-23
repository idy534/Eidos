# Eidos Plugin Manifest v1

```json
{
  "schemaVersion": 1,
  "id": "example-plugin",
  "name": "Example Plugin",
  "version": "0.1.0",
  "description": "Local Eidos extension",
  "skills": [{"root": "skills/example-skill"}],
  "mcpServers": [{
    "id": "example-server",
    "executable": "python3",
    "argv": ["server.py"],
    "envNames": [],
    "permissionProfile": "workspace_read",
    "startupTimeoutSeconds": 15,
    "toolTimeoutSeconds": 60,
    "enabled": false
  }]
}
```

The declaration objects are closed. The package root also accepts optional `hooks`, `interface`, `author`, `homepage`, `license` and `onboardingSkill` fields. Runtime import owns full validation; `validate_plugin.py` only runs package preflight. Skill metadata uses the shared Runtime YAML parser and can include rich YAML and optional fields.

Relative executable paths must name a regular file included in the plugin. `${PLUGIN_ROOT}`, `${CLAUDE_PLUGIN_ROOT}` and `${RUNTIME_PYTHON}` are resolved by the existing MCP execution chain. Environment bindings contain source/target names, never credential values. Import never executes a server or installer. A package at another version can update the current plugin; the new version starts disabled and requires fresh Hook trust and MCP consent.

For a remote Eidos v1 declaration:

```json
{"id":"example","transport":"streamable-http","url":"https://example.com/mcp","auth":{"type":"oauth"},"permissionProfile":"connector","enabled":true}
```

For portable `mcp.json`, use `{"mcpServers":{"example":{"type":"streamable-http","url":"https://example.com/mcp","extensions":{"com.eidos":{"auth":{"type":"bearer"}}}}}}`. Authentication types are `none`, `bearer`, `oauth` and `github-device`. Public OAuth configuration can include `clientId`, `clientMetadataUrl` and `scopes`. No declaration accepts access tokens, refresh tokens or client secrets. OAuth discovery, DCR, CIMD and PKCE use the official SDK. The built-in GitHub package requires its own registered public Client ID for Device Flow; without it, users can connect with a PAT in settings.

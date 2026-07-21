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

The objects are closed: unknown fields are rejected. Relative executable paths must name a regular file included in the plugin. Import never executes the server or any installer.

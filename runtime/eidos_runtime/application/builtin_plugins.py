"""Bundled packages use the same managed import, versioning and consent as local packages."""
from eidos_runtime.extensions.plugins import PluginCatalog, _content_hash, read_bundled_plugin
from eidos_runtime.extensions.plugin_package import read_package
from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.models.mcp_connections import BuiltinPlugin, BuiltinPluginRequest, BuiltinPluginsResponse


def builtin_plugins(plugins: PluginCatalog, request: BuiltinPluginRequest) -> BuiltinPluginsResponse:
    packages = [(name, read_bundled_plugin(name)) for name in ("github", "browser")]
    entries = [(name, files, read_package(files).manifest) for name, files in packages]
    if request.action == "install":
        name = next((name for name, _files, manifest in entries if manifest.id == request.plugin_id), None)
        if name is None:
            raise ApplicationError("RESOURCE_NOT_FOUND")
        record = plugins.import_bundled(name)
        plugins.activate(str(record["id"]), str(record["contentHash"]))
    result = []
    for _name, files, manifest in entries:
        record = plugins.store.plugin_record(manifest.id)
        installed = record is not None and record["status"] == "installed"
        result.append(BuiltinPlugin(id=manifest.id, name=manifest.name, version=manifest.version,
            description=manifest.description, installed=installed,
            update_available=installed and record["contentHash"] != _content_hash(files)))
    return BuiltinPluginsResponse(plugins=result)

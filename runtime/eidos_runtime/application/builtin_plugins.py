"""Bundled packages use the same managed import, versioning and consent as local packages."""
from pathlib import Path

from eidos_runtime.extensions.plugins import PluginCatalog
from eidos_runtime.extensions.plugins import _read_source, _content_hash
from eidos_runtime.extensions.plugin_package import read_package
from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.models.mcp_connections import BuiltinPlugin, BuiltinPluginRequest, BuiltinPluginsResponse


def builtin_plugins(plugins: PluginCatalog, request: BuiltinPluginRequest) -> BuiltinPluginsResponse:
    resources = Path(__file__).resolve().parents[1] / "resources" / "plugins"
    packages = [(resources / name, _read_source(resources / name)) for name in ("github",)]
    entries = [(root, files, read_package(files).manifest) for root, files in packages]
    if request.action == "install":
        source = next((root for root, _files, manifest in entries if manifest.id == request.plugin_id), None)
        if source is None:
            raise ApplicationError("RESOURCE_NOT_FOUND")
        record = plugins.import_directory(source)
        plugins.set_enabled(str(record["id"]), True)
    result = []
    for _root, files, manifest in entries:
        record = plugins.store.plugin_record(manifest.id)
        installed = record is not None and record["status"] == "installed"
        result.append(BuiltinPlugin(id=manifest.id, name=manifest.name, version=manifest.version,
            description=manifest.description, installed=installed,
            update_available=installed and record["contentHash"] != _content_hash(files)))
    return BuiltinPluginsResponse(plugins=result)

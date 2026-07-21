from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import uuid

from pydantic import ValidationError

from eidos_runtime.db.storage import ResourceNotFoundError, SessionStore
from eidos_runtime.extensions.contracts import PluginManifestV1


MAX_PLUGIN_FILES = 512
MAX_PLUGIN_FILE_BYTES = 1024 * 1024
MAX_PLUGIN_TOTAL_BYTES = 8 * 1024 * 1024
MANIFEST_NAME = "plugin.json"


class PluginImportError(ValueError):
    pass


class PluginCatalog:
    def __init__(self, store: SessionStore) -> None:
        if store.data_directory is None:
            raise RuntimeError("storage_not_initialized")
        self.store = store
        self.root = store.data_directory / "extensions" / "plugins"
        _private_directory(self.root)

    def import_directory(self, source: Path) -> dict[str, object]:
        try:
            files = _read_source(source)
            manifest_bytes = files[MANIFEST_NAME]
            manifest_value = json.loads(
                manifest_bytes.decode("utf-8"), object_pairs_hook=_unique_object
            )
            manifest = PluginManifestV1.model_validate(manifest_value)
            _validate_declared_files(manifest, files)
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError, ValidationError):
            raise PluginImportError("plugin_manifest_invalid") from None
        content_hash = _content_hash(files)
        existing = self.store.plugin_record(manifest.id)
        if existing is not None:
            if (
                existing["version"] == manifest.version
                and existing["contentHash"] == content_hash
                and existing["status"] == "installed"
            ):
                return existing
            if existing["version"] == manifest.version:
                raise PluginImportError("plugin_version_conflict")
            raise PluginImportError("plugin_id_conflict")

        destination = self._installed_path(
            manifest.id, manifest.version, content_hash
        )
        _private_directory(destination.parent)
        temporary = destination.parent / f".import-{uuid.uuid4().hex}"
        try:
            _install_files(temporary, files)
            os.replace(temporary, destination)
            _fsync_directory(destination.parent)
            record = self.store.insert_plugin_record({
                "id": manifest.id,
                "name": manifest.name,
                "version": manifest.version,
                "description": manifest.description,
                "manifestJson": json.dumps(
                    manifest.model_dump(mode="json", by_alias=True),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                "contentHash": content_hash,
            })
        except Exception:
            _remove_tree(temporary, self.root)
            _remove_tree(destination, self.root)
            raise
        return record

    def list_plugins(
        self, *, include_removed: bool = False
    ) -> list[dict[str, object]]:
        return self.store.list_plugin_records(include_removed=include_removed)

    def set_enabled(self, plugin_id: str, enabled: bool) -> dict[str, object]:
        return self.store.set_plugin_enabled(plugin_id, enabled)

    def remove(self, plugin_id: str) -> dict[str, object]:
        current = self.store.plugin_record(plugin_id)
        if current is None:
            raise ResourceNotFoundError("plugin not found")
        if current["status"] == "removed":
            return current
        removed = self.store.remove_plugin_record(plugin_id)
        if not self.store.plugin_referenced_by_nonterminal_run(
            plugin_id, str(current["contentHash"])
        ):
            _remove_tree(self._path_from_record(current), self.root)
        return removed

    def extension_snapshot(self) -> dict[str, object]:
        plugins: list[dict[str, object]] = []
        skills: list[dict[str, object]] = []
        servers: list[dict[str, object]] = []
        for record in self.list_plugins():
            if not record["enabled"]:
                continue
            manifest = self.manifest(str(record["id"]))
            plugins.append({
                "id": record["id"],
                "version": record["version"],
                "contentHash": record["contentHash"],
            })
            skills.extend({
                "pluginId": record["id"], "root": skill.root,
                "pluginHash": record["contentHash"],
            } for skill in manifest.skills)
            servers.extend({
                "pluginId": record["id"],
                **server.model_dump(mode="json", by_alias=True),
                "consented": self.store.mcp_server_state(
                    str(record["id"]), server.id
                )["consented"],
            } for server in manifest.mcp_servers)
        plugins.sort(key=lambda value: str(value["id"]).encode("utf-8"))
        skills.sort(key=lambda value: (
            str(value["pluginId"]).encode("utf-8"),
            str(value["root"]).encode("utf-8"),
        ))
        servers.sort(key=lambda value: (
            str(value["pluginId"]).encode("utf-8"),
            str(value["id"]).encode("utf-8"),
        ))
        return {
            "schemaVersion": 1,
            "extensionContractVersion": 1,
            "plugins": plugins,
            "skillCatalogHash": _json_hash(skills),
            "mcpConfigHash": _json_hash(servers),
        }

    def list_mcp_servers(self) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        for record in self.list_plugins():
            manifest = self.manifest(str(record["id"]))
            for config in manifest.mcp_servers:
                state = self.store.mcp_server_state(str(record["id"]), config.id)
                projection = {
                    "schemaVersion": 1,
                    "pluginId": record["id"],
                    "pluginVersion": record["version"],
                    "pluginHash": record["contentHash"],
                    "serverId": config.id,
                    "executable": config.executable,
                    "argv": list(config.argv),
                    "envNames": list(config.env_names),
                    "permissionProfile": config.permission_profile,
                    "startupTimeoutSeconds": config.startup_timeout_seconds,
                    "toolTimeoutSeconds": config.tool_timeout_seconds,
                    "declaredEnabled": config.enabled,
                    "consented": state["consented"],
                    "available": bool(
                        record["enabled"] and config.enabled and state["consented"]
                        and state["errorCode"] is None
                    ),
                    "errorCode": state["errorCode"],
                    "updatedAt": state["updatedAt"],
                }
                if projection["errorCode"] is None:
                    projection.pop("errorCode")
                result.append(projection)
        return sorted(result, key=lambda value: (
            str(value["pluginId"]).encode("utf-8"),
            str(value["serverId"]).encode("utf-8"),
        ))

    def set_mcp_enabled(
        self, plugin_id: str, server_id: str, enabled: bool
    ) -> dict[str, object]:
        record = self.store.plugin_record(plugin_id)
        if record is None or record["status"] != "installed" or not record["enabled"]:
            raise ResourceNotFoundError("plugin not found")
        server = next((value for value in self.list_mcp_servers() if (
            value["pluginId"] == plugin_id and value["serverId"] == server_id
        )), None)
        if server is None:
            raise ResourceNotFoundError("mcp server not found")
        if enabled and not server["declaredEnabled"]:
            raise PluginImportError("mcp_server_disabled_by_manifest")
        return self.store.set_mcp_server_state(server, consented=enabled)

    def cleanup_removed(self) -> None:
        for record in self.list_plugins(include_removed=True):
            if record["status"] != "removed" or self.store.plugin_referenced_by_nonterminal_run(
                str(record["id"]), str(record["contentHash"])
            ):
                continue
            _remove_tree(self._path_from_record(record), self.root)

    def installed_root(self, plugin_id: str) -> Path:
        record = self.store.plugin_record(plugin_id)
        if record is None:
            raise ResourceNotFoundError("plugin not found")
        return self._path_from_record(record)

    def manifest(self, plugin_id: str) -> PluginManifestV1:
        root = self.installed_root(plugin_id)
        try:
            value = json.loads(
                _read_installed_text(root / MANIFEST_NAME),
                object_pairs_hook=_unique_object,
            )
            return PluginManifestV1.model_validate(value)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValidationError):
            raise PluginImportError("plugin_unavailable") from None

    def _path_from_record(self, record: dict[str, object]) -> Path:
        return self._installed_path(
            str(record["id"]), str(record["version"]), str(record["contentHash"])
        )

    def _installed_path(self, plugin_id: str, version: str, content_hash: str) -> Path:
        return self.root / plugin_id / version / content_hash


def _read_source(source: Path) -> dict[str, bytes]:
    try:
        root = source.resolve(strict=True)
        metadata = source.lstat()
    except OSError:
        raise PluginImportError("plugin_source_invalid") from None
    if source.is_symlink() or not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise PluginImportError("plugin_source_invalid")
    files: dict[str, bytes] = {}
    total = 0
    for directory, names, filenames in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        for name in [*names, *filenames]:
            candidate = directory_path / name
            try:
                entry = candidate.lstat()
            except OSError:
                raise PluginImportError("plugin_source_invalid") from None
            if stat.S_ISLNK(entry.st_mode) or entry.st_uid != os.getuid():
                raise PluginImportError("plugin_source_invalid")
            if name in names:
                if not stat.S_ISDIR(entry.st_mode):
                    raise PluginImportError("plugin_source_invalid")
                continue
            if not stat.S_ISREG(entry.st_mode) or entry.st_size > MAX_PLUGIN_FILE_BYTES:
                raise PluginImportError("plugin_source_invalid")
            relative = candidate.relative_to(root).as_posix()
            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            try:
                descriptor = os.open(candidate, flags)
                try:
                    opened = os.fstat(descriptor)
                    if (
                        not stat.S_ISREG(opened.st_mode)
                        or opened.st_uid != os.getuid()
                        or (opened.st_dev, opened.st_ino) != (entry.st_dev, entry.st_ino)
                    ):
                        raise PluginImportError("plugin_source_invalid")
                    data = os.read(descriptor, MAX_PLUGIN_FILE_BYTES + 1)
                finally:
                    os.close(descriptor)
            except OSError:
                raise PluginImportError("plugin_source_invalid") from None
            total += len(data)
            files[relative] = data
            if (
                len(data) > MAX_PLUGIN_FILE_BYTES
                or len(files) > MAX_PLUGIN_FILES
                or total > MAX_PLUGIN_TOTAL_BYTES
            ):
                raise PluginImportError("plugin_source_invalid")
    return files


def _validate_declared_files(
    manifest: PluginManifestV1, files: dict[str, bytes]
) -> None:
    for skill in manifest.skills:
        if f"{skill.root}/SKILL.md" not in files:
            raise PluginImportError("plugin_manifest_invalid")
    for server in manifest.mcp_servers:
        executable = PurePosixPath(server.executable)
        if len(executable.parts) > 1 and not executable.is_absolute():
            if server.executable not in files:
                raise PluginImportError("plugin_manifest_invalid")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise PluginImportError("plugin_manifest_invalid")
        result[key] = value
    return result


def _content_hash(files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for relative in sorted(files, key=lambda value: value.encode("utf-8")):
        encoded = relative.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(len(files[relative]).to_bytes(8, "big"))
        digest.update(files[relative])
    return digest.hexdigest()


def _read_installed_text(path: Path) -> str:
    try:
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_size > MAX_PLUGIN_FILE_BYTES
        ):
            raise OSError("unsafe installed file")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise OSError("installed file changed")
            data = os.read(descriptor, MAX_PLUGIN_FILE_BYTES + 1)
        finally:
            os.close(descriptor)
    except OSError:
        raise PluginImportError("plugin_unavailable") from None
    if len(data) > MAX_PLUGIN_FILE_BYTES:
        raise PluginImportError("plugin_unavailable")
    return data.decode("utf-8")


def _json_hash(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _install_files(destination: Path, files: dict[str, bytes]) -> None:
    destination.mkdir(mode=0o700)
    for relative, data in files.items():
        target = destination.joinpath(*PurePosixPath(relative).parts)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(target.parent, 0o700)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(target, flags, 0o600)
        try:
            offset = 0
            while offset < len(data):
                offset += os.write(descriptor, data[offset:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    _fsync_directory(destination)


def _private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove_tree(path: Path, root: Path) -> None:
    if not path.exists():
        return
    resolved_root = root.resolve()
    resolved = path.resolve()
    if resolved_root not in resolved.parents:
        raise RuntimeError("plugin_storage_boundary")
    shutil.rmtree(resolved)

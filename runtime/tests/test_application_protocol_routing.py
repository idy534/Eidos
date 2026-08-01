from __future__ import annotations

from io import StringIO

from eidos_runtime.protocol.server import RuntimeServer


def test_business_method_registry_has_no_legacy_server_handler_adapter(tmp_path) -> None:
    """Business RPCs must enter application use cases, not captured server calls."""

    server = RuntimeServer(StringIO(), data_directory=tmp_path / "data")

    handlers = {
        registration.name: registration.handler.__class__.__name__
        for registration in server.method_registry
    }

    assert set(handlers.values()) <= {
        "_ApplicationMethodAdapter",
        "_DeferredPluginImportAdapter",
    }
    assert all(registration.error_mapper is not None for registration in server.method_registry)
    assert not {
        "create_session",
        "list_sessions",
        "read_session",
        "rename_session",
        "delete_session",
        "start_run",
        "cancel_run",
        "list_plugins",
        "import_plugin",
    } & set(RuntimeServer.__dict__)

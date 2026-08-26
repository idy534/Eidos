from __future__ import annotations

from types import SimpleNamespace

from eidos_runtime.extensions.skill_dependencies import (
    diagnose_skill_mcp_dependencies,
    render_skill_dependency_warning,
)
from eidos_runtime.extensions.skill_manifest import (
    SkillAgentMetadata,
    SkillDependencies,
    SkillToolDependency,
)


class _Catalog:
    def __init__(self, dependencies: tuple[SkillToolDependency, ...]) -> None:
        self._metadata = SkillAgentMetadata(
            dependencies=SkillDependencies(dependencies)
        )

    def metadata(self, _snapshot: object, _qualified_id: str) -> SkillAgentMetadata:
        return self._metadata


def _snapshot() -> dict[str, object]:
    return {
        "plugins": [{
            "id": "demo",
            "version": "1.0.0",
            "contentHash": "plugin-hash",
        }]
    }


def _server(*, available: bool) -> dict[str, object]:
    return {
        "pluginId": "demo",
        "pluginHash": "plugin-hash",
        "serverId": "documents",
        "executable": "document-mcp",
        "available": available,
    }


def test_mcp_dependency_diagnostics_distinguish_installed_missing_and_unsupported() -> None:
    dependencies = (
        SkillToolDependency(
            type="mcp", value="demo:documents", transport="stdio",
            command="document-mcp",
        ),
        SkillToolDependency(
            type="mcp", value="missing", transport="stdio",
        ),
        SkillToolDependency(
            type="mcp", value="remote", transport="streamable_http",
            url="https://example.com/mcp",
        ),
        SkillToolDependency(type="cli", value="python"),
    )

    diagnostics = diagnose_skill_mcp_dependencies(
        _Catalog(dependencies),  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
        ("user:documents",),
        [_server(available=True)],
        _snapshot(),
    )

    assert [(item.value, item.status) for item in diagnostics] == [
        ("demo:documents", "installed"),
        ("missing", "missing"),
        ("remote", "unsupported"),
    ]
    warning = render_skill_dependency_warning(diagnostics)
    assert warning is not None
    assert warning.role == "user"
    assert "missing" in warning.content
    assert "remote" in warning.content
    assert "did not install or enable" in warning.content


def test_configured_but_unavailable_mcp_dependency_is_missing() -> None:
    diagnostics = diagnose_skill_mcp_dependencies(
        _Catalog((SkillToolDependency(type="mcp", value="documents"),)),  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
        ("user:documents",),
        [_server(available=False)],
        _snapshot(),
    )

    assert diagnostics[0].status == "missing"
    assert diagnostics[0].reason == "configured_but_unavailable"


def test_server_outside_the_run_snapshot_does_not_satisfy_dependency() -> None:
    diagnostics = diagnose_skill_mcp_dependencies(
        _Catalog((SkillToolDependency(type="mcp", value="documents"),)),  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
        ("user:documents",),
        [_server(available=True)],
        {"plugins": []},
    )

    assert diagnostics[0].status == "missing"
    assert diagnostics[0].reason == "mcp_server_not_configured"

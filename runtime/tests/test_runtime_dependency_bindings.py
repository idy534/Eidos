from __future__ import annotations

import hashlib
import json
from pathlib import Path
import threading
from unittest.mock import Mock, patch

import pytest

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.extensions.skill_manifest import SkillAgentMetadata
from eidos_runtime.infrastructure.runtime_dependencies import (
    RuntimeDependencyCatalog,
)
from eidos_runtime.models.runtime_dependencies import RuntimeDependencyBinding
from eidos_runtime.models.skill_runtime import (
    PythonPackageRequirement,
    RuntimeRequirements,
)
from eidos_runtime.model.client import ModelToolCall
from eidos_runtime.runtime import runtime_dependencies as runtime_dependencies_module
from eidos_runtime.runtime.approval import ApprovalCoordinator, ApprovalDecision
from eidos_runtime.runtime.events import RuntimeEvents
from eidos_runtime.runtime.runtime_dependencies import (
    RuntimeDependencyCoordinator,
    RuntimeDependencyPathError,
    RuntimeDependencyVerificationError,
)
from eidos_runtime.runtime.state_machine import RuntimePhaseTracker
from eidos_runtime.runtime.tool_dispatcher import ToolDispatcher
from eidos_runtime.runtime.tool_execution import ToolExecutionController
from eidos_runtime.runtime.tool_runtime import (
    ShellToolHandler,
    _HandlerDependencies,
)
from eidos_runtime.sandbox.permissions import BasePermissionProfile
from eidos_runtime.sandbox.sensitive import default_scanner
from eidos_runtime.tools.contracts import (
    RunShellInput,
    RunShellResultData,
)
from eidos_runtime.tools.workspace import ToolExecutor


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_file(
    root: Path,
    relative_path: str,
    contents: bytes,
    *,
    executable: bool = False,
) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(contents)
    if executable:
        path.chmod(0o755)
    return path


def _make_bundle(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "runtime-bundle"
    root.mkdir()
    _write_file(root, "bin/python3", b"#!/bin/sh\n", executable=True)
    _write_file(root, "bin/node", b"#!/bin/sh\n", executable=True)
    _write_file(root, "dependencies/node/runtime-loader.mjs", b"export {};\n")
    _write_file(
        root,
        "dependencies/python/docx/__init__.py",
        b"__version__ = '1.2.0'\n",
    )
    _write_file(
        root,
        "dependencies/python/python_docx-1.2.0.dist-info/METADATA",
        b"Metadata-Version: 2.1\nName: python-docx\nVersion: 1.2.0\n",
    )
    _write_file(
        root,
        "dependencies/node/node_modules/tool/package.json",
        b'{"name":"tool","version":"1.4.2"}\n',
    )
    inventory_paths = (
        "bin/python3",
        "bin/node",
        "dependencies/node/runtime-loader.mjs",
        "dependencies/python/docx/__init__.py",
        "dependencies/python/python_docx-1.2.0.dist-info/METADATA",
        "dependencies/node/node_modules/tool/package.json",
    )
    payload = {
        "schemaVersion": 1,
        "bundleId": "eidos-runtime",
        "bundleVersion": "1.0.0",
        "target": "darwin-arm64",
        "executables": [
            {
                "name": "python3",
                "path": "bin/python3",
                "version": "3.12.13",
                "sha256": _sha256(root / "bin/python3"),
            },
            {
                "name": "node",
                "path": "bin/node",
                "version": "22.12.0",
                "sha256": _sha256(root / "bin/node"),
            },
        ],
        "pythonPath": ["dependencies/python"],
        "pythonPackages": [
            {
                "name": "python-docx",
                "importName": "docx",
                "version": "1.2.0",
            }
        ],
        "nodeModules": "dependencies/node/node_modules",
        "nodeLoader": "dependencies/node/runtime-loader.mjs",
        "nodePackages": [{"name": "tool", "version": "1.4.2"}],
        "nativeBinPaths": ["bin"],
        "files": [
            {"path": path, "sha256": _sha256(root / path)}
            for path in inventory_paths
        ],
    }
    manifest = root / "runtime.json"
    manifest.write_text(
        json.dumps(payload, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )
    return root, manifest


def _requirements() -> RuntimeRequirements:
    return RuntimeRequirements(
        schemaVersion=1,
        dependencies=(
            PythonPackageRequirement(
                kind="python-package",
                name="python-docx",
                importName="docx",
                version=">=1,<2",
            ),
        ),
    )


def _make_run(tmp_path: Path) -> tuple[SessionStore, str]:
    data = tmp_path / "data"
    workspace = tmp_path / "workspace"
    data.mkdir(mode=0o700)
    workspace.mkdir()
    store = SessionStore(data)
    store.initialize()
    session = store.create_session(str(workspace))
    run, _item = store.create_run(session["id"], "dependency test")
    return store, str(run["id"])


class _ShellRuntimeContext:
    def __init__(self) -> None:
        self.handler: ShellToolHandler | None = None

    def invoke_shell(
        self,
        runtime: object,
        run_id: str,
        item: dict[str, object],
        call: ModelToolCall,
        cancel: threading.Event,
    ):
        assert self.handler is not None
        return self.handler.execute(run_id, item, call, cancel, runtime)


def _make_shell_controller(
    tmp_path: Path,
    catalog: RuntimeDependencyCatalog,
    *,
    approval_request=None,
):
    store, run_id = _make_run(tmp_path)
    store.increment_model_step(run_id)
    workspace = tmp_path / "workspace"
    executor = ToolExecutor(workspace)
    dispatcher = ToolDispatcher(executor.registry)
    events = RuntimeEvents(lambda _message: None)
    state = RuntimePhaseTracker()
    approval = ApprovalCoordinator(
        store,
        approval_request
        or (lambda _request, _cancel: ApprovalDecision("approve")),
        events,
        state,
        lambda _run_id: None,
        lambda: None,
        lambda _run_id, _cancel: None,
        lambda _run_id, _cancel: None,
        requeue=False,
    )
    context = _ShellRuntimeContext()
    controller = ToolExecutionController(
        store,
        dispatcher,
        context,
        events,
        default_scanner(),
        approval=approval,
    )
    coordinator = RuntimeDependencyCoordinator.for_run(
        store,
        run_id,
        catalog=catalog,
        events=events,
    )
    binding = coordinator.default_binding()
    assert binding is not None
    dependencies = _HandlerDependencies(
        store,
        dispatcher,
        events,
        default_scanner(),
        True,
        controller.execute_side_effect,
        controller.authorize_side_effect,
        controller.execute_workspace_side_effect,
        controller.authorize_workspace_side_effect,
        base_permissions=BasePermissionProfile.for_workspace(
            workspace_root=workspace
        ),
        runtime_dependencies=coordinator,
    )
    context.handler = ShellToolHandler(dependencies)
    return store, run_id, executor, controller, dispatcher, binding


def _shell_arguments(binding_id: str, **overrides: object) -> dict[str, object]:
    arguments: dict[str, object] = {
        "command": "fixture",
        "cwd": ".",
        "dependencyBindingId": binding_id,
        "timeoutSeconds": 120,
        "networkAccess": "default",
        "sandboxPermissions": "use_default",
        "additionalPermissions": None,
        "justification": None,
    }
    arguments.update(overrides)
    return arguments


def _execute_shell(
    store: SessionStore,
    run_id: str,
    controller: ToolExecutionController,
    dispatcher: ToolDispatcher,
    arguments: dict[str, object],
    fake_shell,
):
    item = store.create_tool_item(
        run_id,
        1,
        0,
        "shell-call",
        "run_shell",
        json.dumps(arguments),
    )
    call = ModelToolCall("shell-call", "run_shell", arguments)
    with patch(
        "eidos_runtime.runtime.tool_runtime.run_shell",
        side_effect=fake_shell,
    ):
        return controller.execute(
            run_id=run_id,
            item=item,
            call=call,
            plan=dispatcher.plan(call),
            cancel=threading.Event(),
            deadline=None,
        )


def test_real_catalog_resolves_strict_default_binding(tmp_path: Path) -> None:
    bundle, manifest = _make_bundle(tmp_path)
    catalog = RuntimeDependencyCatalog.from_manifest(manifest)
    coordinator = RuntimeDependencyCoordinator("run-1", catalog)

    first = coordinator.default_binding()
    second = coordinator.default_binding()

    assert isinstance(first, RuntimeDependencyBinding)
    assert first == second
    assert first.requirements is None
    assert first.context_id == "run-1:default"
    assert coordinator.catalog_snapshot is catalog.snapshot()
    assert str(bundle.resolve()) in coordinator.catalog_snapshot.runtime_roots


def test_coordinator_uses_packaged_runtime_json_at_app_parent(tmp_path: Path) -> None:
    app = tmp_path / "Eidos.app" / "Contents" / "Resources" / "app"
    module_path = app / "eidos_runtime" / "runtime" / "runtime_dependencies.py"
    module_path.parent.mkdir(parents=True)
    manifest = app.parent / "runtime.json"
    manifest.write_text("{}", encoding="utf-8")

    with patch.object(runtime_dependencies_module, "__file__", str(module_path)):
        assert runtime_dependencies_module._discover_manifest_path() == manifest


def test_bad_manifest_disables_bindings_without_failing_run(tmp_path: Path) -> None:
    store, run_id = _make_run(tmp_path)
    try:
        coordinator = RuntimeDependencyCoordinator.for_run(
            store,
            run_id,
            manifest_path=tmp_path / "missing-runtime.json",
        )

        assert coordinator.available is False
        assert coordinator.default_binding() is None
        assert coordinator.catalog_error is not None
        assert coordinator.catalog_error.code == "manifest_invalid"
    finally:
        store.close()


def test_for_run_defers_implicit_catalog_discovery(tmp_path: Path) -> None:
    store, run_id = _make_run(tmp_path)
    try:
        with (
            patch.object(
                runtime_dependencies_module,
                "_discover_manifest_path",
                side_effect=AssertionError("catalog discovery must be lazy"),
            ) as discover_manifest,
            patch.object(
                RuntimeDependencyCatalog,
                "from_manifest",
                side_effect=AssertionError("manifest loading must be lazy"),
            ) as from_manifest,
        ):
            coordinator = RuntimeDependencyCoordinator.for_run(store, run_id)

        assert coordinator.catalog_snapshot is None
        assert coordinator.available is False
        discover_manifest.assert_not_called()
        from_manifest.assert_not_called()
    finally:
        store.close()


def test_warning_without_runtime_dependency_declaration_does_not_discover_catalog(
    tmp_path: Path,
) -> None:
    store, run_id = _make_run(tmp_path)
    skills = Mock()
    skills.metadata.return_value = SkillAgentMetadata()
    try:
        with (
            patch.object(
                runtime_dependencies_module,
                "_discover_manifest_path",
                side_effect=AssertionError("catalog discovery must be lazy"),
            ) as discover_manifest,
            patch.object(
                RuntimeDependencyCatalog,
                "from_manifest",
                side_effect=AssertionError("manifest loading must be lazy"),
            ) as from_manifest,
        ):
            coordinator = RuntimeDependencyCoordinator.for_run(
                store,
                run_id,
                skills=skills,
                skill_snapshot=object(),
            )
            assert coordinator.skill_dependency_warning(("system:plain",)) is None

        skills.metadata.assert_called_once()
        discover_manifest.assert_not_called()
        from_manifest.assert_not_called()
    finally:
        store.close()


def test_bound_shell_passes_verified_environment_and_result_provenance(
    tmp_path: Path,
) -> None:
    _bundle, manifest = _make_bundle(tmp_path)
    catalog = RuntimeDependencyCatalog.from_manifest(manifest)
    store, run_id, executor, controller, dispatcher, binding = (
        _make_shell_controller(tmp_path, catalog)
    )
    captured: dict[str, object] = {}

    def fake_shell(*args: object, **kwargs: object) -> dict[str, object]:
        captured["dependency_environment"] = kwargs["dependency_environment"]
        callback = args[5]
        assert callable(callback)
        callback("ready\n")
        return {
            "outcome": "success",
            "code": "ok",
            "summary": "Command completed",
            "data": {
                "exitCode": 0,
                "stdout": "ready\n",
                "stderr": "",
                "truncated": False,
                "termination": "exit",
            },
            "sideEffectsMayExist": True,
        }

    try:
        with (
            patch(
                "eidos_runtime.runtime.tool_runtime.is_seatbelt_ready",
                return_value=True,
            ),
            patch.object(
                catalog,
                "verify_binding",
                wraps=catalog.verify_binding,
            ) as verify_binding,
        ):
            outcome = _execute_shell(
                store,
                run_id,
                controller,
                dispatcher,
                _shell_arguments(binding.binding_id),
                fake_shell,
            )
        environment = captured["dependency_environment"]
        assert environment is not None
        assert environment.binding_id == binding.binding_id
        assert outcome.result["data"]["dependencyBinding"]["bindingId"] == (
            binding.binding_id
        )
        assert outcome.result["reconciliationRequired"] is False
        assert verify_binding.call_count == 2
    finally:
        executor.close()
        store.close()


def test_invalid_bound_shell_is_not_started_and_never_reconciles(
    tmp_path: Path,
) -> None:
    _bundle, manifest = _make_bundle(tmp_path)
    catalog = RuntimeDependencyCatalog.from_manifest(manifest)
    store, run_id, executor, controller, dispatcher, _binding = (
        _make_shell_controller(tmp_path, catalog)
    )
    calls: list[object] = []

    def fake_shell(*_args: object, **_kwargs: object) -> dict[str, object]:
        calls.append(True)
        raise AssertionError("invalid dependency binding must not spawn Shell")

    try:
        with patch(
            "eidos_runtime.runtime.tool_runtime.is_seatbelt_ready",
            return_value=True,
        ):
            outcome = _execute_shell(
                store,
                run_id,
                controller,
                dispatcher,
                _shell_arguments("a" * 64),
                fake_shell,
            )
        assert calls == []
        assert outcome.result["data"].get("termination") == "not_started", outcome.result
        assert outcome.result["reconciliationRequired"] is False
        assert outcome.result["sideEffectsMayExist"] is False
    finally:
        executor.close()
        store.close()


def test_bound_shell_rejects_unsandboxed_execution_without_starting(
    tmp_path: Path,
) -> None:
    _bundle, manifest = _make_bundle(tmp_path)
    catalog = RuntimeDependencyCatalog.from_manifest(manifest)
    store, run_id, executor, controller, dispatcher, binding = (
        _make_shell_controller(tmp_path, catalog)
    )
    calls: list[object] = []

    def fake_shell(*_args: object, **_kwargs: object) -> dict[str, object]:
        calls.append(True)
        raise AssertionError("unsandboxed dependency binding must not spawn Shell")

    try:
        arguments = _shell_arguments(
            binding.binding_id,
            sandboxPermissions="require_escalated",
            justification="native access",
        )
        outcome = _execute_shell(
            store,
            run_id,
            controller,
            dispatcher,
            arguments,
            fake_shell,
        )
        assert calls == []
        assert outcome.result["code"] == "dependency_binding_unsandboxed_forbidden"
        assert outcome.result["data"]["termination"] == "not_started", outcome.result
        assert outcome.result["reconciliationRequired"] is False
    finally:
        executor.close()
        store.close()


def test_bound_shell_reverifies_after_approval_before_spawn(tmp_path: Path) -> None:
    _bundle, manifest = _make_bundle(tmp_path)
    catalog = RuntimeDependencyCatalog.from_manifest(manifest)
    loader = tmp_path / "runtime-bundle" / "dependencies/node/runtime-loader.mjs"

    def approve(_request: dict[str, object], _cancel: threading.Event) -> ApprovalDecision:
        loader.write_text("export const changed = true;\n", encoding="utf-8")
        return ApprovalDecision("approve")

    store, run_id, executor, controller, dispatcher, binding = _make_shell_controller(
        tmp_path,
        catalog,
        approval_request=approve,
    )
    calls: list[object] = []

    def fake_shell(*_args: object, **_kwargs: object) -> dict[str, object]:
        calls.append(True)
        raise AssertionError("changed binding must not spawn Shell")

    try:
        arguments = _shell_arguments(
            binding.binding_id,
            sandboxPermissions="with_additional_permissions",
            additionalPermissions={
                "fileSystem": [{
                    "path": str(tmp_path / "workspace"),
                    "access": "read",
                    "recursive": True,
                }],
                "network": None,
            },
            justification="read workspace",
        )
        with patch(
            "eidos_runtime.runtime.tool_runtime.is_seatbelt_ready",
            return_value=True,
        ):
            outcome = _execute_shell(
                store,
                run_id,
                controller,
                dispatcher,
                arguments,
                fake_shell,
            )
        assert calls == []
        assert outcome.result["data"].get("termination") == "not_started", outcome.result
        assert outcome.result["reconciliationRequired"] is False
        assert outcome.result["code"] in {
            "binding_verification_failed",
            "binding_snapshot_changed",
        }
    finally:
        executor.close()
        store.close()


def test_coordinator_rejects_dependency_paths_inside_eidos_data_root(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "eidos-data"
    data_root.mkdir()
    bundle, manifest = _make_bundle(data_root)
    catalog = RuntimeDependencyCatalog.from_manifest(manifest)

    with pytest.raises(RuntimeDependencyPathError):
        RuntimeDependencyCoordinator(
            "run-1",
            catalog,
            forbidden_roots=(data_root,),
        )
    assert bundle.parent == data_root


def test_real_catalog_and_store_persist_two_skill_bindings_and_restore_created_at(
    tmp_path: Path,
) -> None:
    bundle, manifest = _make_bundle(tmp_path)
    catalog = RuntimeDependencyCatalog.from_manifest(manifest)
    store, run_id = _make_run(tmp_path)
    notifications: list[dict[str, object]] = []
    events = RuntimeEvents(
        lambda message: notifications.append(message),
        store=store,
    )
    requirements = _requirements()
    try:
        coordinator = RuntimeDependencyCoordinator.for_run(
            store,
            run_id,
            catalog=catalog,
            events=events,
        )
        first = coordinator.resolve(
            requirements,
            context_id=f"{run_id}:skill-a",
            qualified_skill_id="system:skill-a",
        )
        second = coordinator.resolve(
            requirements,
            context_id=f"{run_id}:skill-b",
            qualified_skill_id="system:skill-b",
        )

        assert isinstance(first, RuntimeDependencyBinding)
        assert isinstance(second, RuntimeDependencyBinding)
        assert first.binding_id != second.binding_id
        assert first.context_id != second.context_id
        assert store.read_runtime_dependency_snapshot(run_id) is not None
        records = store.list_runtime_dependency_bindings(run_id)
        assert {record.qualified_skill_id for record in records} == {
            "system:skill-a",
            "system:skill-b",
        }
        assert len(notifications) >= 3
        first_record = next(
            record for record in records if record.binding_id == first.binding_id
        )
        snapshot_record = store.read_runtime_dependency_snapshot(run_id)
        assert snapshot_record is not None

        projected = coordinator.permission_profile(
            BasePermissionProfile.for_workspace(workspace_root=tmp_path),
            first,
        )
        assert str(bundle.resolve()) in projected.runtime_roots
        assert str(bundle.resolve()) in projected.protected_write_paths
        first_created_at = first_record.created_at
        snapshot_created_at = snapshot_record.created_at
        store.close()

        restored_store = SessionStore(tmp_path / "data")
        restored_store.initialize()
        restored_notifications: list[dict[str, object]] = []
        restored_events = RuntimeEvents(
            lambda message: restored_notifications.append(message),
            store=restored_store,
        )
        try:
            restored = RuntimeDependencyCoordinator.for_run(
                restored_store,
                run_id,
                catalog=catalog,
                events=restored_events,
            ).resolve(
                requirements,
                context_id=f"{run_id}:skill-a",
                qualified_skill_id="system:skill-a",
            )
            restored_record = next(
                record
                for record in restored_store.list_runtime_dependency_bindings(run_id)
                if record.binding_id == first.binding_id
            )
            restored_snapshot = restored_store.read_runtime_dependency_snapshot(run_id)
            assert restored == first
            assert restored_record.created_at == first_created_at
            assert restored_snapshot is not None
            assert restored_snapshot.created_at == snapshot_created_at
            assert restored_notifications == []
        finally:
            restored_store.close()
    finally:
        if store.health_state != "closed":
            store.close()


def test_cross_run_binding_is_rejected(tmp_path: Path) -> None:
    bundle, manifest = _make_bundle(tmp_path)
    catalog = RuntimeDependencyCatalog.from_manifest(manifest)
    first = RuntimeDependencyCoordinator("run-1", catalog).resolve(
        _requirements(),
        context_id="run-1:skill-a",
    )
    second = RuntimeDependencyCoordinator("run-2", catalog)

    with pytest.raises(RuntimeDependencyVerificationError, match="another Run"):
        second.verify_binding(first)
    assert bundle.exists()


def test_run_shell_contract_accepts_bounded_binding_id_but_not_a_path() -> None:
    parsed = RunShellInput.model_validate({
        "command": "python3 scripts/generated.py",
        "dependencyBindingId": "a" * 64,
    })

    assert parsed.dependencyBindingId == "a" * 64
    with pytest.raises(ValueError):
        RunShellInput.model_validate({
            "command": "python3 scripts/generated.py",
            "dependencyBindingId": "../runtime",
        })


def test_unbound_shell_contract_preserves_legacy_argument_projection() -> None:
    parsed = RunShellInput.model_validate({"command": "printf ready"})

    assert "dependencyBindingId" not in parsed.model_dump(
        mode="json", by_alias=True
    )
    bound = RunShellInput.model_validate({
        "command": "python3 scripts/generated.py",
        "dependencyBindingId": "a" * 64,
    })
    assert bound.model_dump(mode="json", by_alias=True)["dependencyBindingId"] == (
        "a" * 64
    )


def test_shell_result_dependency_provenance_is_typed_and_bounded() -> None:
    result = RunShellResultData.model_validate({
        "exitCode": 0,
        "stdout": "",
        "stderr": "",
        "truncated": False,
        "termination": "exited",
        "workspaceChanged": False,
        "dependencyBinding": {
            "bindingId": "a" * 64,
            "manifestSha256": "b" * 64,
            "requirementsSha256": "c" * 64,
            "source": "eidos-runtime",
        },
    })

    assert result.dependencyBinding is not None
    assert result.dependencyBinding.bindingId == "a" * 64

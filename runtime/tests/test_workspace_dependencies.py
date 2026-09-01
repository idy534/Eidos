from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import threading

import pytest

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.client import ModelToolCall
from eidos_runtime.runtime.events import RuntimeEvents
from eidos_runtime.runtime.errors import bounded_tool_result, safe_tool_result
from eidos_runtime.runtime.tool_dispatcher import ToolDispatcher
from eidos_runtime.runtime.tool_execution import HandlerOutcome, ToolExecutionController
from eidos_runtime.sandbox.sensitive import default_scanner
from eidos_runtime.tools.registry import ToolRegistry
from eidos_runtime.tools.workspace_dependencies import (
    WorkspaceDependencyCatalog,
    WorkspaceDependencyError,
    WorkspacePythonPackage,
    workspace_dependencies_entry,
)


def _executable(path: Path) -> Path:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _packages() -> tuple[WorkspacePythonPackage, ...]:
    return (
        WorkspacePythonPackage(
            name="python-docx",
            importName="docx",
            version="1.2.0",
        ),
    )


def test_catalog_returns_canonical_verified_runtime_dependencies(tmp_path: Path) -> None:
    python = _executable(tmp_path / "python3")
    ripgrep = _executable(tmp_path / "rg")
    packages = tmp_path / "packages"
    packages.mkdir()

    snapshot = WorkspaceDependencyCatalog(
        python_executable=python,
        python_paths=(packages,),
        ripgrep_executable=ripgrep,
        owner_uid=os.getuid(),
        python_version="3.12.13",
        ripgrep_version="14.1.1",
        python_packages=_packages(),
    ).snapshot()

    assert snapshot.source == "eidos_runtime"
    assert [entry.name for entry in snapshot.executables] == ["python3", "rg"]
    assert snapshot.executables[0].path == str(python.resolve(strict=True))
    assert snapshot.executables[1].path == str(ripgrep.resolve(strict=True))
    assert snapshot.python_path == (str(packages.resolve(strict=True)),)
    assert snapshot.python_packages[0].import_name == "docx"


def test_catalog_rejects_untrusted_or_non_executable_dependency(tmp_path: Path) -> None:
    python = tmp_path / "python3"
    python.write_text("not executable", encoding="utf-8")
    ripgrep = _executable(tmp_path / "rg")

    with pytest.raises(WorkspaceDependencyError, match="dependency_not_executable"):
        WorkspaceDependencyCatalog(
            python_executable=python,
            python_paths=(),
            ripgrep_executable=ripgrep,
            owner_uid=os.getuid(),
            python_version="3.12.13",
            ripgrep_version="14.1.1",
            python_packages=_packages(),
        ).snapshot()


def test_catalog_omits_missing_python_paths_without_inventing_locations(
    tmp_path: Path,
) -> None:
    python = _executable(tmp_path / "python3")
    ripgrep = _executable(tmp_path / "rg")

    snapshot = WorkspaceDependencyCatalog(
        python_executable=python,
        python_paths=(tmp_path / "missing",),
        ripgrep_executable=ripgrep,
        owner_uid=os.getuid(),
        python_version="3.12.13",
        ripgrep_version="14.1.1",
        python_packages=_packages(),
    ).snapshot()

    assert snapshot.python_path == ()


def test_workspace_dependencies_tool_projects_verified_paths(tmp_path: Path) -> None:
    python = _executable(tmp_path / "python3")
    ripgrep = _executable(tmp_path / "rg")
    packages = tmp_path / "packages"
    packages.mkdir()
    catalog = WorkspaceDependencyCatalog(
        python_executable=python,
        python_paths=(packages,),
        ripgrep_executable=ripgrep,
        owner_uid=os.getuid(),
        python_version="3.12.13",
        ripgrep_version="14.1.1",
        python_packages=_packages(),
    )
    entry = workspace_dependencies_entry(lambda: catalog)

    registry = ToolRegistry((entry,))
    result = registry.get("workspace_dependencies").adapter.execute({}, __import__("threading").Event())
    validated = entry.validate_result(result)

    assert result["outcome"] == "success"
    assert result["data"]["source"] == "eidos_runtime"
    assert result["data"]["pythonPath"] == [str(packages.resolve(strict=True))]
    assert [value["name"] for value in result["data"]["executables"]] == [
        "python3",
        "rg",
    ]
    assert result["data"]["pythonPackages"] == [
        {"name": "python-docx", "importName": "docx", "version": "1.2.0"}
    ]
    assert validated["data"]["pythonPath"] == result["data"]["pythonPath"]


def test_workspace_dependencies_result_keeps_binding_metadata_during_finalization(
    tmp_path: Path,
) -> None:
    python = _executable(tmp_path / "python3")
    ripgrep = _executable(tmp_path / "rg")
    catalog = WorkspaceDependencyCatalog(
        python_executable=python,
        python_paths=(),
        ripgrep_executable=ripgrep,
        owner_uid=os.getuid(),
        python_version="3.12.13",
        ripgrep_version="14.1.1",
        python_packages=(),
    )
    entry = workspace_dependencies_entry(
        lambda: catalog,
        lambda: {
            "defaultDependencyBindingId": "default-binding",
            "activeSkillDependencyBindings": [],
        },
    )
    result = entry.adapter.execute({}, __import__("threading").Event())

    bounded = bounded_tool_result(
        "workspace_dependencies",
        result,
        data_model=entry.result_data_model,
    )
    finalized = safe_tool_result(
        default_scanner(),
        "workspace_dependencies",
        bounded,
        data_model=entry.result_data_model,
    )

    assert finalized["data"]["defaultDependencyBindingId"] == "default-binding"
    assert finalized["data"]["activeSkillDependencyBindings"] == []


def test_workspace_dependencies_controller_commits_the_specific_result_model(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    workspace = tmp_path / "workspace"
    data_root.mkdir(mode=0o700)
    workspace.mkdir()
    store = SessionStore(data_root)
    store.initialize()
    session = store.create_session(str(workspace))
    run, _ = store.create_run(session["id"], "workspace dependencies")
    store.increment_model_step(run["id"])

    python = _executable(tmp_path / "python3")
    ripgrep = _executable(tmp_path / "rg")
    catalog = WorkspaceDependencyCatalog(
        python_executable=python,
        python_paths=(),
        ripgrep_executable=ripgrep,
        owner_uid=os.getuid(),
        python_version="3.12.13",
        ripgrep_version="14.1.1",
        python_packages=(),
    )
    entry = workspace_dependencies_entry(
        lambda: catalog,
        lambda: {
            "defaultDependencyBindingId": "default-binding",
            "activeSkillDependencyBindings": [],
        },
    )
    registry = ToolRegistry((entry,))
    dispatcher = ToolDispatcher(registry)

    class RuntimeContext:
        def invoke_read(
            self, runtime, run_id, item, call, cancel
        ) -> HandlerOutcome:
            prepared = runtime.prepare(self, call.arguments, cancel)
            raw = runtime.execute(self, prepared, cancel)
            verified = runtime.verify(self, prepared, raw, cancel)
            return HandlerOutcome(verified.result, "completed")

    item = store.create_tool_item(
        run["id"],
        1,
        0,
        "provider-call",
        "workspace_dependencies",
        "{}",
    )
    call = ModelToolCall("provider-call", "workspace_dependencies", {})
    controller = ToolExecutionController(
        store,
        dispatcher,
        RuntimeContext(),
        RuntimeEvents(lambda _message: None),
        default_scanner(),
    )

    try:
        outcome = controller.execute(
            run_id=run["id"],
            item=item,
            call=call,
            plan=dispatcher.plan(call),
            cancel=threading.Event(),
            deadline=None,
        )
        persisted = store.read_item(str(outcome.item["id"]))
        committed = json.loads(persisted["toolCall"]["resultJson"])
    finally:
        store.close()

    assert outcome.tool_status == "completed"
    assert committed["outcome"] == "success"
    assert committed["data"]["defaultDependencyBindingId"] == "default-binding"
    assert committed["data"]["activeSkillDependencyBindings"] == []


def test_runtime_workspace_python_can_import_document_dependency() -> None:
    snapshot = WorkspaceDependencyCatalog.from_runtime().snapshot()
    python = next(value for value in snapshot.executables if value.name == "python3")
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(snapshot.python_path)

    completed = subprocess.run(
        [python.path, "-c", "import docx; print(docx.__version__)"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env=environment,
    )

    assert completed.returncode == 0, completed.stderr

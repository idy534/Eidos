"""Opt-in real-account acceptance using only a synthetic temporary workspace.

The caller supplies a private, already authenticated Qoder config directory.
No credentials, prompts, source files or provider error bodies enter the report.
The SDK archive comes from the local fixed release build, through the production
managed-resource verifier. This command spends the account's Qoder Credits.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import tempfile
import threading
import time

import httpx2 as httpx

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.infrastructure.managed_resources import ManagedResourceManager, load_resource_index
from eidos_runtime.model.client import FunctionToolDefinition, ModelRequestError, ModelUsage
from eidos_runtime.model.config import MODEL_CATALOG, ModelConfigStore, QoderModelSettings
from eidos_runtime.model.gateway import ModelGateway
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel
from eidos_runtime.runtime.engine import RuntimeEngine
from eidos_runtime.runtime.resource_registry import ResourceRegistry

ROOT = Path(__file__).resolve().parents[1]


class ArchiveStream(httpx.SyncByteStream):
    def __init__(self, path: Path) -> None:
        self.path = path

    def __iter__(self):
        with self.path.open("rb") as source:
            while chunk := source.read(256 * 1024):
                yield chunk


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--auth-directory", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    index = load_resource_index(ROOT / "build/macos-runtime/resource-index.json")
    report = {"sdk": "1.0.17", "cli": "1.1.79", "cases": {}, "credits": 0.0}
    with tempfile.TemporaryDirectory(prefix="eidos-qoder-acceptance-") as folder:
        work = Path(folder).resolve()
        import os
        os.environ["EIDOS_PROJECTLESS_ROOT"] = str(work / "projectless")
        manager = ManagedResourceManager(index, root=work / "Resource", client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=ArchiveStream(
                ROOT / "build/resource-packages" / request.url.path.rsplit("/", 1)[-1])))))
        manager.ensure(("qoder",), threading.Event())
        report["cases"]["resource_install"] = manager.is_ready("qoder")
        store = ModelConfigStore(work / "config")
        store.initialize()
        config = store.create(provider_id="qoder", model_id="efficient", api_key="", qoder=QoderModelSettings())
        profile = MODEL_CATALOG.profile(config.id, config=config).snapshot(config)
        # Copy the already authenticated test account into a temporary private
        # data directory. Do not change the original login state.
        assert store.path is not None
        shutil.copytree(args.auth_directory.resolve(), store.path.parent / "qoder/default",
                        ignore=shutil.ignore_patterns("work", "__pycache__"))
        resources = ResourceRegistry()
        kernel = RuntimeAsyncKernel(resource_registry=resources)
        kernel.start()
        gateway = ModelGateway(store, async_kernel=kernel, managed_resources=manager, resource_registry=resources)
        lease = gateway.acquire_lease(config, frozen_profile=profile)
        model = lease.client
        driver = model._driver
        report["cases"]["model_gateway"] = lease.provider == "qoder" and model.profile_snapshot == profile
        usage = []
        try:
            text = model.complete(({"type": "user", "isCurrentInput": True,
                "content": "Translate exactly to English: 这只是一次模型接入测试。 Do not use tools."},),
                threading.Event(), lambda _delta: None, instructions="Translate the current user input precisely.",
                allow_tools=False, max_output_tokens=256)
            report["cases"]["text"] = bool(text.text) and not text.tool_calls
            usage.append(text.usage)
            definitions = tuple(FunctionToolDefinition(name=name, description="Return a requested fixture key.",
                parameters_json_schema={"type": "object", "properties": {"key": {"type": "string"}},
                                       "required": ["key"], "additionalProperties": False})
                for name in ("lookup_alpha", "lookup_beta"))
            batch = model.complete(({"type": "user", "isCurrentInput": True,
                "content": "Request both lookup_alpha with key alpha and lookup_beta with key beta in one response. Do not answer without both requests."},),
                threading.Event(), lambda _delta: None, instructions="Follow the current task exactly.", tool_definitions=definitions, max_output_tokens=512)
            report["cases"]["parallel_tool_requests"] = [c.name for c in batch.tool_calls] == ["lookup_alpha", "lookup_beta"]
            usage.append(batch.usage)
            structured = model.complete(({"type": "user", "isCurrentInput": True, "content": "Return key EIDOS_REQUIRED_TOOL_OK."},),
                threading.Event(), lambda _delta: None, instructions="Return the supplied key through the output port.",
                tool_definitions=(definitions[0],), required_tool=definitions[0].name, max_output_tokens=512)
            report["cases"]["required_tool"] = structured.tool_calls[0].arguments == {"key": "EIDOS_REQUIRED_TOOL_OK"}
            usage.append(structured.usage)
            workspace = work / "workspace"
            workspace.mkdir()
            (workspace / "fixture.txt").write_text("EIDOS_PRODUCTION_LOOP_OK_842\n")
            database = SessionStore(work / "data")
            database.initialize()
            try:
                session = database.create_session(str(workspace))
                run, _ = database.create_run(session["id"],
                    "只调用 read_file 读取 fixture.txt，然后只回复文件里的校验词。请不要猜测，也不要调用其他工具。",
                    model_id=config.id, model_profile=profile)
                RuntimeEngine(database, model, lambda _event: None).run(run["id"], threading.Event())
                rows = database.read_session_snapshot(session["id"])
                report["cases"]["runtime_loop"] = database.read_run(run["id"])["status"] == "succeeded" and any(
                    i.get("kind") == "assistant_message" and "EIDOS_PRODUCTION_LOOP_OK_842" in str(i.get("content")) for i in rows["items"])
                report["tool_calls"] = database.connection.execute("SELECT count(*) FROM tool_calls").fetchone()[0]
                for row in database.connection.execute("SELECT usage_json FROM model_attempts WHERE usage_json IS NOT NULL"):
                    usage.append(ModelUsage.model_validate_json(row[0]))
            finally:
                database.close()
            cancel = threading.Event()
            timer = threading.Timer(1, cancel.set)
            timer.start()
            started = time.monotonic()
            try:
                model.complete(({"type": "user", "content": "Write 1000 numbered sentences.", "isCurrentInput": True},),
                    cancel, lambda _text: None, instructions="Follow the current user request.", allow_tools=False)
                report["cases"]["cancel"] = False
            except ModelRequestError as error:
                report["cases"]["cancel"] = error.failure.code == "sampling_canceled" and driver._process is None
            finally:
                timer.cancel()
                timer.join()
            report["cancel_seconds"] = round(time.monotonic() - started, 3)
        finally:
            lease.close()
            kernel.close()
            resources.ensure_empty()
            report["cases"]["resource_quiescence"] = True
        report["credits"] = sum(u.credits_usage.amount for u in usage if u and u.credits_usage)
        report["tokens_unavailable"] = all(u is None or u.input_tokens is None for u in usage)
    report["passed"] = all(report["cases"].values())
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()

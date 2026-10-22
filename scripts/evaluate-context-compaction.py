"""Replay synthetic long histories in isolated storage; optionally call a real model.

This measures retention, grounded continuation, usage and latency. It does not
claim to measure successful completion of real repository development tasks.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import tempfile
import threading
import subprocess
import sys

from pydantic import Field

from eidos_runtime.context.builder import ContextBuilder
from eidos_runtime.context.compactor import ContextCompactor
from eidos_runtime.model.client import FunctionToolDefinition, ModelRequestError, ModelRequestFailure
from eidos_runtime.model.config import ModelConfigStore, default_profile_snapshot
from eidos_runtime.model.gateway import ModelGateway
from eidos_runtime.model.gateway_types import RetryPolicy
from eidos_runtime.models import EidosFrozenStrictModel
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.persistence.context_management import ContextManagementRepository
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel
from eidos_runtime.runtime.cancellation import DeadlineCancellation
from eidos_runtime.runtime.engine import RuntimeEngine
import time


class ContinuationCheck(EidosFrozenStrictModel):
    decision_adopted: bool
    tests_verified: bool
    target_symbol: str = Field(min_length=1, max_length=100)


class BoundedEvaluationModel:
    def __init__(self, client, max_calls: int):
        self.client = client
        self.profile_snapshot = client.profile_snapshot
        self.max_calls = max_calls
        self.calls = 0
        self.failed_calls = 0
        self.usage_responses = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def generate_title(self, *_args):
        return "Synthetic context evaluation"

    def complete(self, *args, **kwargs):
        if self.calls >= self.max_calls:
            raise ModelRequestError(ModelRequestFailure(code="protocol_error", retryable=False))
        self.calls += 1
        try:
            response = self.client.complete(*args, **kwargs)
        except Exception:
            self.failed_calls += 1
            raise
        if response.usage is not None:
            self.usage_responses += 1
            self.input_tokens += response.usage.input_tokens or 0
            self.output_tokens += response.usage.output_tokens or 0
        if any(call.name in {"run_shell", "write_stdin", "spawn_agent", "request_permissions"} for call in response.tool_calls):
            raise ModelRequestError(ModelRequestFailure(code="protocol_error", retryable=False))
        return response


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", help="Configured model ID; omit for offline retention replay")
    parser.add_argument("--reasoning", choices=["none", "thinking", "low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--data-dir", type=Path, default=Path.home() / ".eidos")
    parser.add_argument("--case", action="append", help="Case name, repeat to select several")
    parser.add_argument("--scales", type=int, choices=range(1, 6), default=5)
    parser.add_argument("--cycles", type=int, choices=range(1, 6), default=3)
    parser.add_argument("--max-calls", type=int, default=12)
    parser.add_argument("--task", action="store_true", help="Continue into an isolated code-fix fixture and verify it externally")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.monotonic()
    cases = json.loads((Path(__file__).parent / "fixtures/context-compaction-cases.json").read_text())
    if args.case:
        cases = [case for case in cases if case["name"] in args.case]
    if not cases:
        raise SystemExit("no matching cases")
    if args.task and not args.model:
        raise SystemExit("task continuation requires --model")
    if args.model and len(cases) * args.scales * (args.cycles * 2 + (6 if args.task else 0)) > args.max_calls:
        raise SystemExit("selected evaluation exceeds max-calls; select fewer cases/scales")
    kernel = lease = model = None
    profile = default_profile_snapshot("deepseek-flash")
    logging.basicConfig(level=logging.ERROR)
    if args.model:
        configs = ModelConfigStore(args.data_dir)
        configs.path = configs.data_directory / "models.json"
        config = configs.get(args.model)
        if config is None or not config.supports_tool_call:
            raise SystemExit("configured tool-capable model unavailable")
        if args.reasoning and (not config.reasoning or args.reasoning not in config.reasoning.selections):
            raise SystemExit("reasoning choice is not declared by this model configuration")
        kernel = RuntimeAsyncKernel()
        kernel.start()
        lease = ModelGateway(configs, async_kernel=kernel).acquire_lease(
            config, max_output_tokens=4096, retry_policy=RetryPolicy(max_attempts=1),
            reasoning_selection=args.reasoning or ("none" if config.reasoning and "none" in config.reasoning.selections else None),
        )
        model = BoundedEvaluationModel(lease.client, args.max_calls)
        profile = model.profile_snapshot
    results = []
    try:
        for case in cases:
            for scale in range(1, args.scales + 1):
                with tempfile.TemporaryDirectory(prefix="eidos-context-eval-") as folder:
                    root = Path(folder)
                    (root / "workspace").mkdir()
                    store = SessionStore(root / "data")
                    store.initialize()
                    try:
                        session = store.create_session(str(root / "workspace"))
                        old, _ = store.create_run(session["id"], case["goal"], model_id=profile.model_id, model_profile=profile)
                        store.fail_run(old["id"], "synthetic_history")
                        correction, _ = store.create_run(session["id"], case["correction"], model_id=profile.model_id, model_profile=profile)
                        store.fail_run(correction["id"], "synthetic_history")
                        run, goal = store.create_run(session["id"], "继续任务。请使用历史中的目标、约束和验证事实。", model_id=profile.model_id, model_profile=profile)
                        target = "inspect_" + case["name"]
                        previous_sources: set[str] = set()
                        previous_notes: set[str] = set()
                        cycles = []
                        raw_digest = hashlib.sha256()
                        raw_items = []
                        for cycle in range(args.cycles):
                            for index in range(12):
                                step = cycle * 14 + index + 1
                                tool = store.create_tool_item(run["id"], step, 0, f"read-{cycle}-{index}", "read_file", "{}")
                                content = f"def {target}(): pass\n" + "\n".join(f"# evidence {line}; trace_{line % 23} = {index}" for line in range(150 * scale))
                                raw = json.dumps({"outcome": "success", "summary": "Read context.py", "data": {"path": "context.py", "content": content}}, ensure_ascii=False)
                                store.complete_tool_item(tool["id"], raw, model_result_json=raw)
                                raw_digest.update(raw.encode())
                                raw_items.append(tool["id"])
                            test = store.create_tool_item(run["id"], cycle * 14 + 13, 0, f"test-{cycle}", "run_shell", "{}")
                            raw = json.dumps({"outcome": "success" if case["verified"] else "error", "summary": "17 passed" if case["verified"] else "pytest was not run; missing environment", "data": {"stdout": "17 passed" if case["verified"] else "", "exitCode": 0 if case["verified"] else 1}})
                            store.complete_tool_item(test["id"], raw)
                            # This unsupported self-report must never become a completed action.
                            assistant = store.create_assistant_item(run["id"], cycle * 14 + 14)
                            store.append_item_content(assistant["id"], "I will verify later. All tests passed. Next inspect unresolved behavior.")
                            store.complete_assistant_item(assistant["id"])
                            builder = ContextBuilder(store)
                            before = builder.build(run["id"]).budget.projected_input_tokens
                            summary = ContextCompactor(store, model).compact(
                                run["id"], "mid_turn", trigger="manual", before_tokens=before,
                                measure=lambda: builder.build(run["id"]).budget.projected_input_tokens,
                            )
                            serialized = summary.model_dump_json()
                            retained = all(value in serialized for value in ("context.py", target, "preserve" if case["name"] == "english_constraints" else "必须"))
                            lineage = previous_sources <= set(summary.source_item_ids) and goal["id"] not in summary.source_item_ids
                            previous_sources = set(summary.source_item_ids)
                            current_notes = {note.model_dump_json() for note in summary.continuity_notes}
                            continuity_preserved = previous_notes <= current_notes
                            previous_notes = current_notes
                            no_false_completion = not any("All tests passed" in value for value in summary.completed_actions)
                            details = ContextManagementRepository(store.database).details(run["id"], profile)
                            record = details.compaction.to_wire_dict()
                            check = None
                            check_usage = None
                            check_answer = None
                            check_failure = None
                            if model:
                                built = builder.build(run["id"])
                                port = FunctionToolDefinition(name="submit_context_check", description="Return historical facts only.", parameters_json_schema=ContinuationCheck.model_json_schema())
                                try:
                                    response = model.complete(
                                        (*built.model_context, {"type": "user", "content": "根据历史原始证据，缓存方案是否已采纳？测试是否已实际通过？目标 Python Symbol 的名称是什么？Assistant 自述不是验证。只调用 submit_context_check 返回答案。"}),
                                        DeadlineCancellation(threading.Event(), time.monotonic() + 60, time.monotonic), lambda _: None,
                                        instructions=built.instructions.system_text, tool_definitions=(port,), max_output_tokens=4096, required_tool=port.name,
                                    )
                                    check_usage = response.usage.model_dump(mode="json") if response.usage else None
                                    if response.finish_reason in {"length", "content_filter", "error"} or response.response_state not in {None, "complete"}:
                                        raise ValueError("incomplete_response")
                                    if len(response.tool_calls) != 1 or response.tool_calls[0].name != port.name:
                                        raise ValueError("missing_structured_answer")
                                    checked = ContinuationCheck.model_validate_json(json.dumps(response.tool_calls[0].arguments))
                                    check_answer = checked.to_wire_dict()
                                    check = checked.decision_adopted == case["adopted"] and checked.tests_verified == case["verified"] and checked.target_symbol == target
                                    if not check:
                                        check_failure = "incorrect_historical_answer"
                                except ModelRequestError as error:
                                    check = False
                                    check_failure = "request_failed:" + error.failure.code
                                except ValueError as error:
                                    check = False
                                    check_failure = error.args[0] if error.args and error.args[0] in {"incomplete_response", "missing_structured_answer"} else "invalid_structured_answer"
                            cycles.append({"retained": retained, "lineage": lineage, "continuity_preserved": continuity_preserved,
                                           "no_false_completion": no_false_completion,
                                           "compaction": record, "continuation_passed": check, "continuation_failure": check_failure,
                                           "continuation_usage": check_usage, "continuation_answer": check_answer})
                        digest = hashlib.sha256()
                        for identifier in raw_items:
                            digest.update(store.read_item(identifier)["toolCall"]["resultJson"].encode())
                        task = None
                        if args.task:
                            workspace = root / "workspace"
                            (workspace / "README.md").write_text("Keep this user-owned fixture unchanged.\n")
                            (workspace / "context.py").write_text(f"def {target}(values):\n    return sum(values) / (len(values) + 1)\n")
                            tests = (f"import unittest\nfrom context import {target}\n"
                                     f"class AverageTests(unittest.TestCase):\n"
                                     f"    def test_empty(self): self.assertIsNone({target}([]))\n"
                                     f"    def test_one(self): self.assertEqual({target}([2]), 2)\n"
                                     f"    def test_many(self): self.assertEqual({target}([2, 4, 6]), 4)\n"
                                     f"    def test_zero(self): self.assertEqual({target}([0, 0]), 0)\n")
                            (workspace / "test_fixture.py").write_text(tests)
                            def verify():
                                return subprocess.run([sys.executable, "-m", "unittest", "-q"], cwd=workspace, capture_output=True, timeout=20).returncode == 0
                            failed_before = not verify()
                            store.fail_run(run["id"], "synthetic_replay_complete")
                            continued, _ = store.create_run(
                                session["id"], f"修复 context.py 的 {target}：空列表返回 None；非空列表返回算术平均数。必须保留 README.md 和 test_fixture.py。不要调用 Shell、运行测试或启动子 Agent。你需要读取当前文件版本后再编辑。最终回复必须说明测试尚未运行。",
                                model_id=profile.model_id, model_profile=profile,
                            )
                            ContextManagementRepository(store.database).request(continued["id"])
                            calls_before = model.calls
                            RuntimeEngine(store, model, lambda _: None).run(continued["id"], DeadlineCancellation(threading.Event(), time.monotonic() + 120, time.monotonic))
                            task = {"kind": "isolated_code_fix", "failed_before": failed_before, "external_tests_passed": verify(),
                                    "run_status": store.read_run(continued["id"])["status"], "model_calls": model.calls - calls_before,
                                    "preserved_files": (workspace / "README.md").read_text() == "Keep this user-owned fixture unchanged.\n" and (workspace / "test_fixture.py").read_text() == tests,
                                    "compaction": ContextManagementRepository(store.database).details(continued["id"], profile).compaction.to_wire_dict()}
                        result = {"case": case["name"], "scale": scale, "originals_unchanged": digest.digest() == raw_digest.digest(), "cycles": cycles, "repository_task": task}
                        results.append(result)
                        print(json.dumps({"case": case["name"], "scale": scale, "passed": result["originals_unchanged"] and all(c["retained"] and c["lineage"] and c["continuity_preserved"] and c["no_false_completion"] and c["continuation_passed"] is not False for c in cycles)}, ensure_ascii=False), flush=True)
                    finally:
                        store.close()
    finally:
        if lease:
            lease.close()
        if kernel:
            kernel.close()
        report = {"kind": "synthetic_history_replay", "model": args.model, "model_calls": model.calls if model else 0,
                  "started_at": started_at, "duration_ms": round((time.monotonic() - started) * 1000),
                  "source_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in (
                      Path(__file__).relative_to(Path.cwd()), Path("scripts/fixtures/context-compaction-cases.json"),
                      *(Path("runtime/eidos_runtime") / path for path in (
                          "context/model_summary.py", "context/summary_evidence.py", "context/facts.py",
                          "context/compactor.py", "context/verified_compaction.py", "context/budget.py",
                          "context/builder.py", "model/context_projection.py", "model/pydantic_ai_client.py", "model/native_custom.py",
                      )),
                  )},
                  "provider_usage": {"responses_with_usage": model.usage_responses, "calls_without_usage": model.calls - model.usage_responses,
                                     "failed_calls": model.failed_calls, "input_tokens": model.input_tokens, "output_tokens": model.output_tokens} if model else None,
                  "reasoning_selection": profile.reasoning_selection,
                  "real_repository_task_completion": "isolated_fixture_only" if args.task else "not_measured", "results": results}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return 0 if results and all(row["originals_unchanged"] and all(c["retained"] and c["lineage"] and c["continuity_preserved"] and c["no_false_completion"] and c["continuation_passed"] is not False for c in row["cycles"])
                               and (row["repository_task"] is None or (row["repository_task"]["failed_before"] and row["repository_task"]["external_tests_passed"] and row["repository_task"]["preserved_files"] and row["repository_task"]["run_status"] == "succeeded")) for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

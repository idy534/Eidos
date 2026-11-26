from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

RUNTIME_ROOT = Path(__file__).resolve().parents[1] / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.db.storage import SessionStore  # noqa: E402
from eidos_runtime.model.client import (  # noqa: E402
    ModelResponse,
    ModelToolCall,
    ScriptedModel,
)
from eidos_runtime.runtime.approval import ApprovalDecision  # noqa: E402
from eidos_runtime.runtime.engine import RuntimeEngine  # noqa: E402

_SECRET_PATTERN = re.compile(
    r'("apiKey"\s*:\s*")([^"]+)(")|(?:sk|ak)-[A-Za-z0-9_-]{8,}',
    re.IGNORECASE,
)


class _StrictEvalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class EvalTurn(_StrictEvalModel):
    user_input: str = Field(alias="userInput", min_length=1)


class EvalToolCallSpec(_StrictEvalModel):
    call_id: str = Field(alias="callId", min_length=1)
    name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


class EvalScriptedStep(_StrictEvalModel):
    text: str = ""
    tool_calls: list[EvalToolCallSpec] = Field(default_factory=list, alias="toolCalls")


class EvalGraders(_StrictEvalModel):
    answer_contains: list[str] = Field(default_factory=list, alias="answerContains")
    file_contains: dict[str, str] = Field(default_factory=dict, alias="fileContains")
    file_assertions: list[str] = Field(default_factory=list, alias="fileAssertions")


class EvalCase(_StrictEvalModel):
    id: str = Field(min_length=1)
    category: Literal["repo_comprehension", "code_delivery", "multi_turn", "reliability"]
    description: str = ""
    files: dict[str, str] = Field(default_factory=dict)
    turns: list[EvalTurn] = Field(min_length=1)
    scripted_steps: list[EvalScriptedStep] = Field(
        default_factory=list, alias="scriptedSteps"
    )
    graders: EvalGraders = Field(default_factory=EvalGraders)


class EvalSuite(_StrictEvalModel):
    version: int = Field(ge=1)
    cases: list[EvalCase] = Field(min_length=1)


class EvalCaseResult(_StrictEvalModel):
    id: str
    category: str
    passed: bool
    turns_executed: int
    duration_seconds: float
    failures: list[str] = Field(default_factory=list)
    final_answer: str = ""


class EvalBatchReport(_StrictEvalModel):
    created_at: str
    mode: Literal["fake_model"]
    total: int
    passed: int
    failed: int
    pass_rate: float
    results: list[EvalCaseResult]


def redact_secrets(payload: str) -> str:
    return _SECRET_PATTERN.sub(
        lambda m: f"{m.group(1)}[REDACTED]{m.group(3)}" if m.group(1) else "[REDACTED]",
        payload,
    )


def load_eval_suite(path: Path) -> EvalSuite:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return EvalSuite.model_validate(raw)


def _to_model_responses(steps: list[EvalScriptedStep]) -> list[ModelResponse]:
    responses: list[ModelResponse] = []
    for step in steps:
        calls = tuple(
            ModelToolCall(call.call_id, call.name, dict(call.arguments))
            for call in step.tool_calls
        )
        responses.append(
            ModelResponse(
                text=step.text,
                tool_calls=calls,
                finish_reason="tool_calls" if calls else "stop",
            )
        )
    return responses


def run_eval_case(case: EvalCase) -> EvalCaseResult:
    started = time.perf_counter()
    failures: list[str] = []
    final_answer = ""
    turns_executed = 0

    with tempfile.TemporaryDirectory(prefix=f"eidos-eval-{case.id}-") as tmp_dir:
        root = Path(tmp_dir)
        data_dir = root / "data"
        workspace = (root / "workspace").resolve()
        data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        workspace.mkdir(parents=True, exist_ok=True)

        for rel_path, content in case.files.items():
            target = (workspace / rel_path).resolve()
            if not target.is_relative_to(workspace):
                raise ValueError(f"Unsafe fixture path: {rel_path}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")

        store = SessionStore(data_dir)
        store.initialize()
        try:
            session = store.create_session(str(workspace))
            session_id = str(session["id"])
            model = ScriptedModel(_to_model_responses(case.scripted_steps))

            for turn in case.turns:
                run, _ = store.create_run(session_id, turn.user_input)
                RuntimeEngine(
                    store,
                    model,
                    lambda _event: None,
                    request_approval=lambda _params, _cancel: ApprovalDecision("approve"),
                ).run(str(run["id"]), threading.Event())
                turns_executed += 1
                snapshot = store.read_session_snapshot(session_id)
                assistant_items = [
                    item
                    for item in snapshot["items"]
                    if item.get("kind") == "assistant_message"
                    and item.get("runId") == run["id"]
                    and isinstance(item.get("content"), str)
                ]
                if assistant_items:
                    final_answer = str(assistant_items[-1]["content"])
                run_row = next(
                    (r for r in snapshot["runs"] if r.get("id") == run["id"]),
                    None,
                )
                if run_row is None or run_row.get("status") != "succeeded":
                    failures.append(
                        f"turn {turns_executed} ended with status={run_row.get('status') if run_row else 'missing'}"
                    )

            for snippet in case.graders.answer_contains:
                if snippet not in final_answer:
                    failures.append(f"final answer missing expected snippet: {snippet!r}")

            for rel_path, expected in case.graders.file_contains.items():
                target = (workspace / rel_path).resolve()
                if not target.is_file():
                    failures.append(f"expected file missing: {rel_path}")
                    continue
                actual_text = target.read_text(encoding="utf-8")
                if expected not in actual_text:
                    failures.append(
                        f"file {rel_path} missing expected snippet: {expected!r}"
                    )
        finally:
            store.close()

    elapsed = round(time.perf_counter() - started, 3)
    return EvalCaseResult(
        id=case.id,
        category=case.category,
        passed=len(failures) == 0,
        turns_executed=turns_executed,
        duration_seconds=elapsed,
        failures=failures,
        final_answer=final_answer,
    )


def run_eval_suite(
    suite_path: Path,
    *,
    case_ids: set[str] | None = None,
    output_dir: Path | None = None,
) -> EvalBatchReport:
    suite = load_eval_suite(suite_path)
    selected = [
        case for case in suite.cases if case_ids is None or case.id in case_ids
    ]
    results = [run_eval_case(case) for case in selected]
    passed = sum(1 for item in results if item.passed)
    total = len(results)
    report = EvalBatchReport(
        created_at=datetime.now(UTC).isoformat(),
        mode="fake_model",
        total=total,
        passed=passed,
        failed=total - passed,
        pass_rate=(passed / total) if total else 0.0,
        results=results,
    )

    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        raw_json = redact_secrets(report.model_dump_json(indent=2))
        (output_dir / "report.json").write_text(raw_json, encoding="utf-8")
        md_lines = [
            "# Eidos Eval Report",
            "",
            f"- Timestamp: {report.created_at}",
            f"- Mode: {report.mode}",
            f"- Passed: {report.passed}/{report.total} ({report.pass_rate:.0%})",
            "",
            "| Case | Category | Result | Turns | Duration (s) | Failures |",
            "|---|---|---|---|---|---|",
        ]
        for item in results:
            status = "PASS" if item.passed else "FAIL"
            failure_text = "; ".join(item.failures) if item.failures else "-"
            md_lines.append(
                f"| {item.id} | {item.category} | {status} | {item.turns_executed} | {item.duration_seconds} | {failure_text} |"
            )
        (output_dir / "report.md").write_text(
            redact_secrets("\n".join(md_lines) + "\n"),
            encoding="utf-8",
        )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Eidos multi-turn evaluation runner")
    parser.add_argument(
        "--suite",
        type=Path,
        default=Path(__file__).resolve().parent / "cases" / "baseline.json",
    )
    parser.add_argument("--case", action="append", dest="cases")
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    case_ids = set(args.cases) if args.cases else None
    report = run_eval_suite(
        args.suite, case_ids=case_ids, output_dir=args.output_dir
    )
    print(f"Eval completed: {report.passed}/{report.total} passed")
    return 0 if report.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

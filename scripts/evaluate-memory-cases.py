"""Replay memory cases against a real model in isolated Eidos data directories.

Cases live in scripts/fixtures/memory-cases.json, grouped as in the memory
optimization design: C mid-task correction, P scope, U update, R recall,
T stale facts, I injected instructions, X other Sessions, E write cost.
Every attempt uses a new temporary data directory and workspace; the script
only reads the model configuration. Checks are judged from SQLite state, tool
calls and the visible answer. Answers still need a human read.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import logging
import os
from pathlib import Path
import tempfile
import threading
import uuid

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.memory.contracts import MemoryEditRequest, MemoryListRequest, MemoryReadRequest
from eidos_runtime.model.config import ModelConfigStore
from eidos_runtime.model.gateway import ModelGateway
from eidos_runtime.model.gateway_types import RetryPolicy
from eidos_runtime.persistence.worktrees import ProjectWorktreeRepository
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel
from eidos_runtime.runtime.engine import RuntimeEngine

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "memory-cases.json"
OUTPUT_PORTS = frozenset({"_eidos_assess_completion"})

parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
parser.add_argument("--model", default="deepseek-v4.1-flash")
parser.add_argument("--data-dir", type=Path, default=Path.home() / ".eidos")
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--group", action="append", help="Run only these groups; may be repeated.")
parser.add_argument("--case", action="append", help="Run only these cases; may be repeated.")
parser.add_argument("--repeats", type=int, default=5, help="Attempts per case (default 5).")
parser.add_argument("--max-requests", type=int, default=24, help="Model requests allowed per attempt.")
parser.add_argument("--run-timeout", type=float, default=120.0, help="Seconds allowed per Run.")
args = parser.parse_args()
logging.basicConfig(level=logging.ERROR)
if args.repeats < 1 or args.max_requests < 1:
    raise SystemExit("repeats and max-requests must be positive")

configs = ModelConfigStore(args.data_dir)
configs.path = configs.data_directory / "models.json"
config = configs.get(args.model)
if config is None or not config.supports_tool_call:
    raise SystemExit("configured model unavailable")
kernel = RuntimeAsyncKernel()
kernel.start()
lease = ModelGateway(configs, async_kernel=kernel).acquire_lease(
    config, max_output_tokens=4096, retry_policy=RetryPolicy(max_attempts=1),
    reasoning_selection="none" if config.reasoning and "none" in config.reasoning.selections else None,
)


class EvalModel:
    """Counts requests and usage, and limits the tools a case may use."""

    profile_snapshot = lease.client.profile_snapshot

    def __init__(self, tools: str) -> None:
        self.tools = tools
        self.requests = 0
        self.tokens = 0

    def generate_title(self, *arguments):
        return "Memory case"

    def complete(self, context, cancel, on_text_delta, **kwargs):
        if self.requests >= args.max_requests:
            raise RuntimeError("eval_request_limit")
        self.requests += 1
        if self.tools == "memory":
            kwargs["tool_definitions"] = tuple(
                tool for tool in kwargs.get("tool_definitions", ())
                if tool.name.startswith("memory_") or tool.name in OUTPUT_PORTS
            )
        response = lease.client.complete(context, cancel, on_text_delta, **kwargs)
        allowed = {tool.name for tool in kwargs.get("tool_definitions", ())}
        if any(call.name not in allowed for call in response.tool_calls):
            raise RuntimeError("eval_unadvertised_tool")
        if response.usage:
            self.tokens += (response.usage.input_tokens or 0) + (response.usage.output_tokens or 0)
        return response


def note_content(note: dict[str, object]) -> str:
    repeat = note.get("repeat")
    if isinstance(repeat, dict):
        return "\n".join([str(repeat["line"])] * int(repeat["count"])) + "\n"
    return str(note["content"])


def seed_note(store: SessionStore, session_id: str, note: dict[str, object]) -> None:
    store.database.memory.edit(MemoryEditRequest(
        session_id=session_id, operation_id=str(uuid.uuid4()), scope=note["scope"], path=note["path"],
        kind=note.get("kind", "feedback"), summary=note.get("summary", ""), content=note_content(note),
    ))


def conversation(store: SessionStore, session_id: str, user: str, assistant: str) -> None:
    run, _ = store.create_run(session_id, user)
    item = store.create_assistant_item(run["id"], 1)
    store.append_item_content(item["id"], assistant)
    store.complete_assistant_item_committed(item["id"])
    store.fail_run(run["id"], "evaluation_history")


def notes(store: SessionStore, session_id: str, scope: str) -> list:
    return store.database.memory.list(MemoryListRequest(session_id=session_id, scope=scope, limit=100)).documents


def all_notes(store: SessionStore, session_id: str, scope: str) -> list:
    found, cursor = [], 0
    while cursor is not None:
        state = store.database.memory.list(
            MemoryListRequest(session_id=session_id, scope=scope, limit=100, cursor=cursor)
        )
        found.extend(state.documents)
        cursor = state.next_cursor
    return found


def body(store: SessionStore, session_id: str, scope: str, path: str) -> str:
    parts, offset = [], 0
    while offset is not None:
        page = store.database.memory.read(MemoryReadRequest(
            session_id=session_id, scope="global" if scope == "global" else "current",
            path=path, offset=offset, limit=16384,
        ))
        parts.append(page.content)
        offset = page.next_offset
    return "".join(parts)


def check(store, session_id, observed, rule) -> bool:
    kind = rule["type"]
    scopes = ["global", "current"] if rule.get("scope") in (None, "any") else [rule["scope"]]
    if kind == "run_succeeded":
        return all(status == "succeeded" for status in observed["run_statuses"])
    if kind in {"note_contains", "note_length_at_least", "note_revised"}:
        for scope in scopes:
            for document in all_notes(store, session_id, scope):
                if rule.get("path") not in (None, document.path):
                    continue
                text = body(store, session_id, scope, document.path)
                if kind == "note_contains" and any(value in text for value in rule["any"]):
                    return True
                if kind == "note_length_at_least" and len(text) >= rule["chars"]:
                    return True
                if kind == "note_revised" and document.revision > 1:
                    return True
        return False
    if kind == "note_count":
        return sum(len(all_notes(store, session_id, scope)) for scope in scopes) == rule["equals"]
    if kind == "no_new_notes":
        total = sum(len(all_notes(store, session_id, scope)) for scope in ("global", "current"))
        return total == observed["seeded_notes"]
    if kind == "answer_contains":
        return any(value in observed["answer"] for value in rule["any"])
    if kind == "answer_not_contains":
        return not any(value in observed["answer"] for value in rule["any"])
    if kind == "tool_called":
        return any(
            call["name"] == rule["name"]
            and all(call["arguments"].get(key) == value for key, value in rule.get("arguments", {}).items())
            for call in observed["tool_calls"]
        )
    if kind == "index_hides":
        return rule["path"] not in observed["index"]
    raise ValueError(f"unknown check {kind}")


def attempt(case: dict[str, object], number: int) -> dict[str, object]:
    model = EvalModel(str(case.get("tools", "memory")))
    with tempfile.TemporaryDirectory(prefix="eidos-memory-case-") as folder:
        root = Path(folder)
        workspace, other = root / "workspace", root / "other"
        workspace.mkdir()
        other.mkdir()
        for relative, content in dict(case.get("workspace", {})).items():
            target = workspace / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        store = SessionStore(root / "data")
        store.initialize()
        try:
            projects = ProjectWorktreeRepository(store.database)
            projects.get_or_create_project(workspace)
            projects.get_or_create_project(other)
            for entry in case.get("history", []):
                past = store.create_session(str(workspace if entry["project"] == "same" else other))
                conversation(store, past["id"], entry["user"], entry["assistant"])
            session = store.create_session(str(workspace))
            seeded = list(case.get("notes", []))
            for note in seeded:
                seed_note(store, session["id"], note)
            # Fillers are corrections, which the index lists before project
            # and reference notes, so a large count pushes those out of it.
            for index in range(int(case.get("filler_notes", 0))):
                seed_note(store, session["id"], {
                    "scope": "current", "path": f"filler-{index:03d}.md", "kind": "feedback",
                    "summary": f"无关条目 {index}", "content": f"与当前问题无关的条目 {index}。",
                })
            index = store.database.memory.project(session["id"], 10**9, run_id="evaluation").rendered_payload
            runs, errors = [], []
            for text in case["turns"]:
                run, _ = store.create_run(
                    session["id"], text, model_id=config.id, model_profile=model.profile_snapshot,
                    work_mode="execute", approval_mode="full_access",
                )
                runs.append(run["id"])
                cancel = threading.Event()
                timer = threading.Timer(args.run_timeout, cancel.set)
                timer.start()
                try:
                    RuntimeEngine(store, model, lambda message: None, async_kernel=kernel).run(run["id"], cancel)
                except Exception as error:  # Record the failure and keep evaluating.
                    errors.append(type(error).__name__)
                    store.fail_run(run["id"], "evaluation_runtime_error")
                finally:
                    timer.cancel()
                    timer.join()
            snapshot = store.read_session_snapshot(session["id"])
            items = [item for item in snapshot["items"] if item["runId"] in runs]
            observed = {
                "run_statuses": [store.read_run(run_id)["status"] for run_id in runs],
                "answer": "\n".join(item.get("content", "") for item in items if item["kind"] == "assistant_message"),
                "tool_calls": [
                    {"name": item["toolCall"]["toolName"], "arguments": json.loads(item["toolCall"].get("argumentsJson") or "{}")}
                    for item in items if item.get("toolCall")
                ],
                "seeded_notes": len(seeded) + int(case.get("filler_notes", 0)),
                "index": index,
            }
            checks = [
                {"type": rule["type"], "passed": check(store, session["id"], observed, rule)}
                for rule in case["checks"]
            ]
            return {
                "case": case["name"], "group": case["group"], "attempt": number,
                "passed": all(value["passed"] for value in checks), "checks": checks,
                "run_statuses": observed["run_statuses"], "errors": errors,
                "requests": model.requests, "tokens": model.tokens,
                "memory_tool_calls": sum(call["name"].startswith("memory_") for call in observed["tool_calls"]),
                "tool_calls": [call["name"] for call in observed["tool_calls"]],
                "answer": observed["answer"][:800],
            }
        finally:
            store.close()


fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
selected = [
    case for case in fixture["cases"]
    if (not args.group or case["group"] in args.group) and (not args.case or case["name"] in args.case)
]
results = []
original_projectless_root = os.environ.get("EIDOS_PROJECTLESS_ROOT")
try:
    for case in selected:
        for number in range(1, args.repeats + 1):
            record = attempt(case, number)
            results.append(record)
            print(json.dumps({key: record[key] for key in ("case", "attempt", "passed", "requests")}, ensure_ascii=False), flush=True)
finally:
    lease.close()
    kernel.close()
    if original_projectless_root is None:
        os.environ.pop("EIDOS_PROJECTLESS_ROOT", None)
    else:
        os.environ["EIDOS_PROJECTLESS_ROOT"] = original_projectless_root
    summary: dict[str, dict[str, int]] = defaultdict(lambda: {"passed": 0, "attempts": 0})
    for record in results:
        for key in (record["case"], "group:" + record["group"]):
            summary[key]["attempts"] += 1
            summary[key]["passed"] += int(record["passed"])
    args.output.write_text(json.dumps({
        "model_id": config.id, "repeats": args.repeats, "summary": summary, "results": results,
        "review_required": "Checks read state, tool calls and answer text only. Read the answers and saved notes before drawing conclusions.",
    }, ensure_ascii=False, indent=2))
    print(json.dumps({"model_id": config.id, "attempts": len(results)}, ensure_ascii=False), flush=True)

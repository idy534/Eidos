from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import threading
import unittest


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.db.storage import SessionStore  # noqa: E402
from eidos_runtime.model.client import ModelResponse, ScriptedModel  # noqa: E402
from eidos_runtime.persistence.worktrees import ProjectWorktreeRepository  # noqa: E402
from eidos_runtime.runtime.events import RuntimeEvents  # noqa: E402
from eidos_runtime.runtime.finalizer import RunFinalizer  # noqa: E402
from eidos_runtime.runtime.provider_control import (  # noqa: E402
    contains_provider_control_syntax,
)
from eidos_runtime.runtime.state_machine import RuntimePhaseTracker  # noqa: E402
from eidos_runtime.sandbox.sensitive import SensitiveScanner  # noqa: E402
from eidos_runtime.tools.runtime_workspace import ToolExecutor  # noqa: E402
from eidos_runtime.workspace.projectless import projectless_root  # noqa: E402


class RuntimeReliabilityRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="eidos-runtime-reliability-"
        )
        root = Path(self.temporary.name)
        self.data = root / "data"
        self.workspace = root / "workspace"
        self.data.mkdir(mode=0o700)
        self.workspace.mkdir()
        self.store = SessionStore(self.data)
        self.store.initialize()
        self.session = self.store.create_session(str(self.workspace))

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_twenty_first_model_step_remains_in_the_same_segment(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "long repository analysis")

        for expected in range(1, 22):
            self.assertEqual(
                self.store.increment_model_step(run["id"]),
                expected,
            )
            self.store.complete_current_step(run["id"], "completed")

        assert self.store.connection is not None
        rows = self.store.connection.execute(
            """
            SELECT ordinal, status, step_count FROM execution_segments
            WHERE run_id = ? ORDER BY ordinal
            """,
            (run["id"],),
        ).fetchall()
        self.assertEqual(
            [(row["ordinal"], row["status"], row["step_count"]) for row in rows],
            [(1, "running", 21)],
        )
        current = self.store.read_run(run["id"])
        self.assertEqual(current["status"], "running")
        self.assertEqual(current["modelStepCount"], 21)

    def test_provider_control_recognizes_deepseek_fullwidth_envelope(self) -> None:
        variants = (
            "<|DSML|tool_calls>",
            "<｜DSML｜tool_calls>",
            "<｜｜DSML｜｜tool_calls>",
            "<||DSML||tool_calls>",
        )

        for value in variants:
            with self.subTest(value=value):
                self.assertTrue(contains_provider_control_syntax(value))
        self.assertFalse(contains_provider_control_syntax("DSML is plain prose"))

    def test_finalizer_discards_provider_control_output(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "finalize safely")
        model = ScriptedModel([
            ModelResponse(
                text=(
                    '<｜｜DSML｜｜tool_calls><｜｜DSML｜｜invoke name="read_file">'
                    '<｜｜DSML｜｜parameter name="path">Cargo.toml'
                )
            )
        ])

        outcome = RunFinalizer(
            self.store,
            model,
            RuntimeEvents(lambda _message: None),
            SensitiveScanner(),
            RuntimePhaseTracker(),
        ).finalize(
            run["id"],
            (),
            "context_still_over_budget",
            threading.Event(),
        )

        self.assertIsNone(outcome.item)
        self.assertEqual(outcome.failure_reason, "finalization_protocol_error")
        attempts = self.store.read_finalization_attempts(run["id"])
        self.assertEqual(attempts[0]["status"], "model_failed")
        self.assertEqual(
            attempts[0]["errorCode"],
            "finalization_protocol_error",
        )
        snapshot = self.store.read_session_snapshot(self.session["id"])
        assistant_text = [
            item.get("content")
            for item in snapshot["items"]
            if item["kind"] == "assistant_message"
        ]
        self.assertFalse(any("DSML" in str(value) for value in assistant_text))

    def test_finalizer_fallback_adds_file_reference_rules_only_for_project_runs(self) -> None:
        project_root = self.workspace / "project"
        project_root.mkdir()
        project = ProjectWorktreeRepository(self.store.database).get_or_create_project(
            project_root
        )
        project_session = self.store.typed_runtime_repository().create_session(
            str(project_root), project_id=project.id
        ).value
        projectless_workspace = projectless_root() / "2026-10-13" / "finalizer"
        projectless_workspace.mkdir(parents=True)
        projectless_session = self.store.typed_runtime_repository().create_session(
            str(projectless_workspace), projectless=True
        ).value

        cases = (
            (projectless_session.id, False),
            (project_session.id, True),
        )
        for session_id, has_project in cases:
            with self.subTest(has_project=has_project):
                run, _ = self.store.create_run(session_id, "Finalize response")
                model = ScriptedModel([ModelResponse(text="Done")])
                outcome = RunFinalizer(
                    self.store,
                    model,
                    RuntimeEvents(lambda _message: None),
                    SensitiveScanner(),
                    RuntimePhaseTracker(),
                ).finalize(
                    run["id"],
                    (),
                    "context_still_over_budget",
                    threading.Event(),
                )

                self.assertIsNone(outcome.failure_reason)
                self.assertEqual(
                    "Project file references" in model.instructions_history[0],
                    has_project,
                )

    @unittest.skipUnless(sys.platform == "darwin", "shell preflight uses macOS fd identity")
    def test_shell_preflight_accepts_codex_style_rust_token_sources(self) -> None:
        (self.workspace / "token_budget.rs").write_text(
            "pub const LIMIT: usize = 1;\n",
            encoding="utf-8",
        )
        executor = ToolExecutor(self.workspace)
        try:
            cwd = executor.prepare_shell(".", threading.Event())
        finally:
            executor.close()

        self.assertEqual(cwd.path, self.workspace.resolve())


if __name__ == "__main__":
    unittest.main()

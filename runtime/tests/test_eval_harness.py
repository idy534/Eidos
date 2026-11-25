from __future__ import annotations

import json
from pathlib import Path

from eval.runner import EvalCase, EvalGraders, EvalScriptedStep, EvalTurn, redact_secrets, run_eval_case, run_eval_suite

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_SUITE = REPO_ROOT / "eval" / "cases" / "baseline.json"


def test_eval_harness_runs_baseline_suite_and_writes_reports(tmp_path: Path) -> None:
    report_dir = tmp_path / "eval-out"
    report = run_eval_suite(BASELINE_SUITE, output_dir=report_dir)
    assert report.total == 3
    assert report.passed == 3, f"Failures: {[r.failures for r in report.results if not r.passed]}"
    assert report.failed == 0
    assert (report_dir / "report.json").is_file()
    assert (report_dir / "report.md").is_file()

    parsed = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
    assert parsed["passed"] == 3
    assert len(parsed["results"]) == 3
    assert parsed["results"][2]["turns_executed"] == 2


def test_eval_harness_detects_failing_grader_and_redacts_secrets() -> None:
    failing_case = EvalCase(
        id="failing-case",
        category="reliability",
        description="Expected snippet missing.",
        files={"note.txt": "hello\n"},
        turns=[EvalTurn(userInput="Read note.txt")],
        scriptedSteps=[EvalScriptedStep(text="Did not include target string.")],
        graders=EvalGraders(answerContains=["MUST_EXIST"]),
    )
    result = run_eval_case(failing_case)
    assert result.passed is False
    assert any("MUST_EXIST" in reason for reason in result.failures)

    leaked = '{"apiKey":"sk-secret1234567890abcd","other":"ok"}'
    redacted = redact_secrets(leaked)
    assert "sk-secret1234567890abcd" not in redacted
    assert "[REDACTED]" in redacted

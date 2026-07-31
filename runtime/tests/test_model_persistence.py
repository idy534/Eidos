from __future__ import annotations

from pathlib import Path
import tempfile
import threading
import unittest


import sys
RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.context.builder import ContextBuilder  # noqa: E402
from eidos_runtime.db.storage import DATABASE_NAME, SessionStore  # noqa: E402
from eidos_runtime.model.client import (  # noqa: E402
    ModelProfileSnapshot,
    ModelResponse,
    ModelUsage,
    ScriptedModel,
)
from eidos_runtime.runtime.engine import RuntimeEngine  # noqa: E402
from eidos_runtime.model.client import (  # noqa: E402
    ModelRequestError,
    ModelRequestFailure,
)


class ModelPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-model-persistence-")
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

    def profile(self, *, context: int = 4_096, output: int = 512) -> ModelProfileSnapshot:
        return ModelProfileSnapshot(
            provider_id="deepseek",
            model_id="deepseek-v4-flash",
            context_window_tokens=context,
            max_output_tokens=output,
            request_timeout_seconds=120.0,
            supports_tools=True,
            supports_json_schema_output=False,
            supports_reasoning=True,
        )

    def test_new_run_freezes_profile_and_context_budget_uses_it(self) -> None:
        run, _ = self.store.create_run(
            self.session["id"],
            "budget",
            model_profile=self.profile(context=6_000, output=1_000),
        )

        profile = self.store.read_model_profile(run["id"])
        budget = ContextBuilder(self.store).build(run["id"]).budget

        self.assertEqual(profile.context_window_tokens, 6_000)
        self.assertEqual(profile.max_output_tokens, 1_000)
        self.assertEqual(budget.usable_input_budget, 6_000 - 1_000 - 1_024)

    def test_later_profile_values_do_not_change_existing_run(self) -> None:
        first, _ = self.store.create_run(
            self.session["id"], "first", model_profile=self.profile(context=6_000)
        )
        self.store.cancel_run(first["id"])
        second, _ = self.store.create_run(
            self.session["id"], "second", model_profile=self.profile(context=9_000)
        )

        self.assertEqual(
            self.store.read_model_profile(first["id"]).context_window_tokens,
            6_000,
        )
        self.assertEqual(
            self.store.read_model_profile(second["id"]).context_window_tokens,
            9_000,
        )

    def test_successful_attempt_persists_usage_and_response_metadata(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "sample")
        model = ScriptedModel([ModelResponse(
            text="done",
            usage=ModelUsage(input_tokens=12, output_tokens=3),
            provider_name="deepseek",
            resolved_model_name="deepseek-v4-flash",
            finish_reason="stop",
            provider_response_id="response-1",
            response_state="complete",
        )])

        RuntimeEngine(self.store, model, lambda _message: None).run(
            run["id"], threading.Event()
        )

        attempt = self.store.read_model_attempts(run["id"])[0]
        self.assertEqual(attempt["status"], "completed")
        self.assertEqual(attempt["usage"].input_tokens, 12)
        self.assertEqual(attempt["providerName"], "deepseek")
        self.assertEqual(attempt["finishReason"], "stop")
        self.assertEqual(attempt["providerResponseId"], "response-1")
        self.assertIsNotNone(attempt["durationMs"])

    def test_recovery_keeps_completed_attempt_metadata(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "recover")
        self.store.increment_model_step(run["id"])
        self.store.complete_current_model_attempt(
            run["id"],
            "completed",
            usage=ModelUsage(input_tokens=5, output_tokens=2),
            provider_name="deepseek",
            finish_reason="stop",
            duration_ms=10,
        )
        self.store.close()
        self.store = SessionStore(self.data)
        self.store.initialize()

        attempt = self.store.read_model_attempts(run["id"])[0]
        self.assertEqual(attempt["status"], "completed")
        self.assertEqual(attempt["usage"].input_tokens, 5)
        self.assertEqual(attempt["finishReason"], "stop")

    def test_stream_progress_does_not_create_a_second_model_attempt(self) -> None:
        class RetryThenSuccess:
            calls = 0

            def complete(self, _context, _cancel, on_text_delta, **_options):
                self.calls += 1
                if self.calls == 1:
                    on_text_delta("safe progress")
                    raise ModelRequestError(ModelRequestFailure(
                        code="provider_unavailable",
                        retryable=True,
                        status_code=503,
                        provider_name="deepseek",
                    ))
                on_text_delta("done")
                return ModelResponse(
                    text="done",
                    usage=ModelUsage(input_tokens=8, output_tokens=2),
                    provider_name="deepseek",
                    resolved_model_name="deepseek-v4-flash",
                    finish_reason="stop",
                    response_state="complete",
                )

        run, _ = self.store.create_run(self.session["id"], "retry")
        model = RetryThenSuccess()
        RuntimeEngine(self.store, model, lambda _message: None).run(
            run["id"], threading.Event()
        )

        attempts = self.store.read_model_attempts(run["id"])
        self.assertEqual(model.calls, 1)
        self.assertEqual(self.store.read_run(run["id"])["modelStepCount"], 1)
        self.assertEqual([item["status"] for item in attempts], ["failed"])
        self.assertEqual(attempts[0]["errorCode"], "provider_unavailable")
        self.assertEqual(attempts[0]["httpStatus"], 503)
        self.assertTrue(attempts[0]["hadProgress"])
        self.assertIsNone(attempts[0]["usage"])
        self.assertEqual(
            attempts[0]["retryDecision"]["reason"], "unsafe_stream_progress"
        )

    def test_each_attempt_persists_its_own_usage(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "attempt usage")
        self.store.increment_model_step(run["id"])
        self.store.complete_current_model_attempt(
            run["id"],
            "failed",
            usage=ModelUsage(input_tokens=4, output_tokens=1),
            error_code="provider_unavailable",
        )
        self.store.start_retry_model_attempt(run["id"])
        self.store.complete_current_model_attempt(
            run["id"],
            "completed",
            usage=ModelUsage(input_tokens=8, output_tokens=2),
        )

        attempts = self.store.read_model_attempts(run["id"])
        self.assertEqual(attempts[0]["usage"].input_tokens, 4)
        self.assertEqual(attempts[1]["usage"].input_tokens, 8)

    def test_length_finish_fails_attempt_and_is_not_normal_completion(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "length")
        model = ScriptedModel([ModelResponse(
            text="truncated",
            finish_reason="length",
            response_state="complete",
        )])

        RuntimeEngine(self.store, model, lambda _message: None).run(
            run["id"], threading.Event()
        )

        attempt = self.store.read_model_attempts(run["id"])[0]
        self.assertEqual(attempt["status"], "failed")
        self.assertEqual(attempt["finishReason"], "length")
        self.assertEqual(attempt["errorCode"], "length")
        self.assertEqual(attempt["retryDecision"]["reason"], "invalid_completion")
        self.assertEqual(self.store.read_run(run["id"])["errorCode"], "MODEL_PROTOCOL_ERROR")

    def test_sensitive_text_split_across_deltas_never_enters_sqlite(self) -> None:
        secret = "sk-abcdefghijklmnop"

        class SplitSecret:
            def complete(self, _context, _cancel, on_text_delta, **_options):
                on_text_delta("safe prefix sk-abcdefgh")
                on_text_delta("ijklmnop\n")
                return ModelResponse(text="safe prefix " + secret)

        run, _ = self.store.create_run(self.session["id"], "scan")
        RuntimeEngine(self.store, SplitSecret(), lambda _message: None).run(
            run["id"], threading.Event()
        )

        self.assertNotIn(secret.encode(), (self.data / DATABASE_NAME).read_bytes())
        attempt = self.store.read_model_attempts(run["id"])[0]
        self.assertEqual(attempt["status"], "failed")
        self.assertEqual(attempt["errorCode"], "sensitive_scan_failed")
        self.assertEqual(attempt["retryDecision"]["reason"], "sensitive_scan_failed")




if __name__ == "__main__":
    unittest.main()

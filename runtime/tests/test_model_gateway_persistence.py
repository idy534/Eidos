from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import os
import tempfile
import threading
import unittest


import sys
RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.db.storage import DATABASE_NAME, SessionStore  # noqa: E402
from eidos_runtime.model_gateway.auth import ModelSecretStore  # noqa: E402
from eidos_runtime.model_gateway.models import (  # noqa: E402
    CapabilityProbeSource,
    CapabilitySnapshot,
    ModelProfile,
    ReasoningMode,
    RetryPolicy,
    RunModelSnapshot,
    WireAPI,
)
from eidos_runtime.model_gateway.gateway import legacy_profile_snapshot  # noqa: E402


NOW = datetime(2026, 7, 30, tzinfo=UTC)


def profile(*, name: str = "DeepSeek", model_id: str = "deepseek-chat") -> ModelProfile:
    return ModelProfile(
        id="profile-1",
        name=name,
        provider="deepseek",
        base_url="https://api.deepseek.com",
        auth_reference="local:credential-1",
        wire_api=WireAPI.OPENAI_CHAT_COMPLETIONS,
        model_id=model_id,
        context_window=128_000,
        max_output_tokens=4_096,
        reasoning_mode=ReasoningMode.NONE,
        request_timeout=30.0,
        retry_policy=RetryPolicy(max_attempts=3),
        created_at=NOW,
        updated_at=NOW,
    )


def capability(value: ModelProfile, *, snapshot_id: str = "capability-1") -> CapabilitySnapshot:
    return CapabilitySnapshot.conservative(
        value,
        snapshot_id=snapshot_id,
        probe_source=CapabilityProbeSource.ACTIVE_PROBE,
        probe_version="r2-v1",
        probed_at=NOW,
        verified={"supports_tools": True},
    )


class ModelGatewayPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-gateway-store-")
        root = Path(self.temporary.name)
        self.data = root / "data"
        self.workspace = root / "workspace"
        self.data.mkdir(mode=0o700)
        self.workspace.mkdir()
        self.store = SessionStore(self.data)
        self.store.initialize()

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_profiles_and_capabilities_are_typed_and_separate(self) -> None:
        original = profile()
        self.store.create_model_profile(original)
        self.store.save_model_capability_snapshot(capability(original))

        updated = original.model_copy(update={"name": "Edited"})
        self.store.update_model_profile(updated)

        self.assertEqual(self.store.get_model_profile(original.id).name, "Edited")
        self.assertEqual(self.store.list_model_profiles()[0].id, original.id)
        saved = self.store.get_model_capability_snapshot(original.id)
        assert saved is not None
        self.assertEqual(saved.id, "capability-1")
        self.assertEqual(saved.model_id, "deepseek-chat")

    def test_profile_deletion_keeps_capability_and_historical_run_snapshot(self) -> None:
        value = profile()
        verified = capability(value)
        self.store.create_model_profile(value)
        self.store.save_model_capability_snapshot(verified)
        session = self.store.create_session(str(self.workspace))
        run, _ = self.store.create_run(session["id"], "snapshot")
        frozen = RunModelSnapshot(profile=value, capability=verified, frozen_at=NOW)
        self.store.save_run_model_snapshot(run["id"], frozen)

        self.store.delete_model_profile(value.id)

        self.assertIsNone(self.store.get_model_profile(value.id))
        self.assertEqual(
            self.store.get_model_capability_snapshot(value.id).id,  # type: ignore[union-attr]
            verified.id,
        )
        self.assertEqual(
            self.store.read_run_model_snapshot(run["id"]).profile.model_id,
            "deepseek-chat",
        )

    def test_raw_secret_is_outside_sqlite_and_resolves_by_reference(self) -> None:
        secrets = ModelSecretStore(self.data)
        secrets.initialize()
        reference = secrets.save("provider-key-value-123456", secret_id="credential-1")
        self.store.create_model_profile(profile())

        self.assertEqual(reference, "local:credential-1")
        self.assertEqual(secrets.resolve(reference), "provider-key-value-123456")
        self.assertNotIn(
            b"provider-key-value-123456",
            (self.data / DATABASE_NAME).read_bytes(),
        )
        assert secrets.path is not None
        self.assertEqual(oct(secrets.path.stat().st_mode & 0o777), "0o600")

    def test_attempts_reference_the_frozen_lease_wire_model_and_retry_decision(self) -> None:
        value = profile()
        verified = capability(value)
        frozen = RunModelSnapshot(profile=value, capability=verified, frozen_at=NOW)
        session = self.store.create_session(str(self.workspace))
        run, _ = self.store.create_run(
            session["id"],
            "attempt metadata",
            model_id=value.model_id,
            model_profile=legacy_profile_snapshot(frozen),
            run_model_snapshot=frozen,
        )
        self.store.increment_model_step(run["id"])
        self.store.complete_current_model_attempt(
            run["id"],
            "failed",
            error_code="MODEL_PROVIDER_UNAVAILABLE",
            retry_decision={"retry": True, "reason": "transient_error"},
        )

        attempt = self.store.read_model_attempts(run["id"])[0]
        self.assertEqual(attempt["leaseId"], frozen.lease_id)
        self.assertEqual(attempt["wireApi"], "openai_chat_completions")
        self.assertEqual(attempt["modelId"], value.model_id)
        self.assertEqual(attempt["requestTimeout"], 30.0)
        self.assertEqual(
            attempt["retryDecision"],
            {"retry": True, "reason": "transient_error"},
        )

    def test_environment_secret_reference_never_writes_secret_file(self) -> None:
        secrets = ModelSecretStore(self.data)
        secrets.initialize()
        os.environ["EIDOS_TEST_PROVIDER_KEY"] = "environment-key-value"
        try:
            self.assertEqual(
                secrets.resolve("env:EIDOS_TEST_PROVIDER_KEY"),
                "environment-key-value",
            )
        finally:
            os.environ.pop("EIDOS_TEST_PROVIDER_KEY", None)
        assert secrets.path is not None
        self.assertFalse(secrets.path.exists())

    def test_cancelled_probe_does_not_create_capability_snapshot(self) -> None:
        from eidos_runtime.model_gateway.capability import CapabilityProbe

        value = profile()
        cancel = threading.Event()
        cancel.set()

        with self.assertRaisesRegex(RuntimeError, "MODEL_PROBE_CANCELLED"):
            CapabilityProbe().probe(value, "provider-key-value-123456", cancel)
        self.assertIsNone(self.store.get_model_capability_snapshot(value.id))


if __name__ == "__main__":
    unittest.main()

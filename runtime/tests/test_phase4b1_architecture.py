from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.runtime.contracts import RuntimeCancelled  # noqa: E402
from eidos_runtime.runtime.tool_dispatcher import ToolDispatcher  # noqa: E402
from eidos_runtime.runtime.tool_execution import ToolConcurrencyGate  # noqa: E402
from eidos_runtime.tools.registry import (  # noqa: E402
    ToolConcurrencyPolicy,
)
from eidos_runtime.tools.workspace import ToolExecutor  # noqa: E402


class Phase4B1ArchitectureTests(unittest.TestCase):
    def test_every_descriptor_is_complete_and_step_binds_exact_instance(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-descriptors-") as root:
            with ToolExecutor(Path(root)) as executor:
                registry = executor.registry
                snapshot = registry.snapshot()
                for descriptor in registry.entries:
                    self.assertFalse(hasattr(descriptor.adapter, "execution_kind"))
                    self.assertIsNotNone(descriptor.runtime)
                    self.assertIsNotNone(descriptor.projector)
                    self.assertIsNotNone(descriptor.execution_policy)
                    self.assertTrue(descriptor.contract_fingerprint)
                    binding = snapshot.binding(descriptor.spec.name)
                    self.assertIsNotNone(binding)
                    assert binding is not None
                    self.assertIs(binding.descriptor, descriptor)
                    self.assertEqual(
                        binding.contract_fingerprint,
                        descriptor.contract_fingerprint,
                    )

        for legacy_method in (
            "prepare_file_change",
            "commit_file_change",
            "prepare_eidos_state",
            "commit_eidos_state",
            "prepare_shell",
            "execute_external",
            "consume_activations",
        ):
            self.assertFalse(hasattr(ToolDispatcher, legacy_method))

    def test_projection_and_execution_policy_change_contract_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-fingerprint-") as root:
            with ToolExecutor(Path(root)) as executor:
                descriptor = executor.registry.get("read_file")
                assert descriptor is not None
                policy = descriptor.execution_policy
                assert policy is not None
                changed = replace(
                    descriptor,
                    execution_policy=policy.model_copy(update={
                        "concurrency": policy.concurrency.model_copy(
                            update={"max_concurrency": 1}
                        ),
                    }),
                )
                self.assertNotEqual(
                    descriptor.contract_fingerprint,
                    changed.contract_fingerprint,
                )

    def test_concurrency_gate_parallel_exclusive_keys_and_cancel_release(self) -> None:
        gate = ToolConcurrencyGate()
        parallel = ToolConcurrencyPolicy.model_validate({
            "mode": "parallel_safe",
            "max_concurrency": 2,
        })
        keyed = parallel.model_copy(update={"resource_keys": ("workspace:a",)})
        exclusive = ToolConcurrencyPolicy.model_validate({
            "mode": "exclusive",
            "max_concurrency": 1,
        })
        cancel = threading.Event()

        first = gate.acquire(parallel, cancel)
        second = gate.acquire(parallel, cancel)
        self.assertEqual(gate.active_permits, 2)
        second.__exit__()
        first.__exit__()

        keyed_first = gate.acquire(keyed, cancel)
        acquired = threading.Event()

        def wait_for_same_key() -> None:
            with gate.acquire(keyed, cancel):
                acquired.set()

        thread = threading.Thread(target=wait_for_same_key)
        thread.start()
        time.sleep(0.03)
        self.assertFalse(acquired.is_set())
        keyed_first.__exit__()
        thread.join(1)
        self.assertTrue(acquired.is_set())

        permit = gate.acquire(exclusive, cancel)
        waiting_cancel = threading.Event()
        canceled: list[bool] = []

        def wait_for_exclusive() -> None:
            try:
                gate.acquire(exclusive, waiting_cancel)
            except RuntimeCancelled:
                canceled.append(True)

        thread = threading.Thread(target=wait_for_exclusive)
        thread.start()
        waiting_cancel.set()
        thread.join(1)
        permit.__exit__()
        self.assertEqual(canceled, [True])
        self.assertEqual(gate.active_permits, 0)

        with self.assertRaisesRegex(RuntimeError, "infrastructure"):
            with gate.acquire(parallel, cancel):
                raise RuntimeError("infrastructure")
        self.assertEqual(gate.active_permits, 0)


if __name__ == "__main__":
    unittest.main()

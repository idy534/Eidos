import unittest

from eidos_runtime.runtime.contracts import ProgressSignature
from eidos_runtime.runtime.loop_guard import LoopGuard


class ReconciliationRecoveryBudgetTests(unittest.TestCase):
    def test_existing_signature_call_defaults_to_no_barrier_and_no_poll(self) -> None:
        signature = LoopGuard().make_signature(
            workspace_version=0,
            diff_hash=None,
            successful_tool_result_hashes=(),
            context_fact_ids=(),
            error_fingerprints=(),
            reconciliation_epoch=0,
        )
        self.assertFalse(signature.reconciliation_required)
        self.assertFalse(signature.managed_shell_poll)

    def _signature(
        self,
        guard: LoopGuard,
        index: int,
        *,
        epoch: int = 1,
        required: bool = True,
        poll: bool = False,
    ) -> ProgressSignature:
        return guard.make_signature(
            workspace_version=0,
            diff_hash=None,
            successful_tool_result_hashes=(f"result-{index}",),
            context_fact_ids=(f"fact-{index}",),
            error_fingerprints=(),
            reconciliation_epoch=epoch,
            reconciliation_required=required,
            managed_shell_poll=poll,
        )

    def test_new_results_and_facts_continue_even_with_the_same_barrier(self) -> None:
        guard = LoopGuard()
        for index in range(12):
            with self.subTest(index=index):
                signature = self._signature(guard, index)
                self.assertEqual(signature.successful_tool_result_hashes, (f"result-{index}",))
                self.assertEqual(signature.new_context_fact_ids, (f"fact-{index}",))
                self.assertIsNone(guard.observe_progress(signature))

    def test_ordinary_progress_has_no_reconciliation_round_limit(self) -> None:
        guard = LoopGuard()
        for index in range(10):
            with self.subTest(index=index):
                self.assertIsNone(
                    guard.observe_progress(self._signature(guard, index, required=False))
                )

    def test_managed_shell_polls_remain_neutral(self) -> None:
        guard = LoopGuard()
        self.assertIsNone(guard.observe_progress(self._signature(guard, 0)))
        self.assertIsNone(guard.observe_progress(self._signature(guard, 1)))
        for index in range(2, 7):
            self.assertIsNone(
                guard.observe_progress(self._signature(guard, index, poll=True))
            )
        self.assertIsNone(guard.observe_progress(self._signature(guard, 7)))

    def test_changing_epoch_does_not_introduce_a_fixed_round_budget(self) -> None:
        guard = LoopGuard()
        self.assertIsNone(guard.observe_progress(self._signature(guard, 0)))
        self.assertIsNone(guard.observe_progress(self._signature(guard, 1)))
        for index in range(2, 12):
            with self.subTest(index=index):
                self.assertIsNone(guard.observe_progress(self._signature(guard, index, epoch=2)))

    def test_restored_signatures_do_not_restore_a_fixed_round_budget(self) -> None:
        guard = LoopGuard()
        signatures = []
        for index in range(2):
            signature = self._signature(guard, index)
            signatures.append(signature)
            self.assertIsNone(guard.observe_progress(signature))

        restored = LoopGuard.from_signatures(tuple(signatures))
        self.assertIsNone(restored.observe_progress(self._signature(restored, 2)))

    def test_unknown_effect_does_not_disable_no_progress_detection(self) -> None:
        guard = LoopGuard()
        self.assertIsNone(guard.observe_progress(self._signature(guard, 0)))
        self.assertEqual(guard.observe_progress(self._signature(guard, 0)), "recover_no_progress")
        guard.mark_recovery_attempted()
        self.assertEqual(guard.observe_progress(self._signature(guard, 0)), "no_progress")

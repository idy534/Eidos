from __future__ import annotations

from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class RuntimeLayoutTests(unittest.TestCase):
    def test_runtime_concerns_are_importable_from_their_owned_packages(self) -> None:
        from eidos_runtime.db.storage import SessionStore
        from eidos_runtime.context.builder import ContextBuilder
        from eidos_runtime.context.compactor import ContextCompactor
        from eidos_runtime.model.client import ModelResponse
        from eidos_runtime.protocol.schemas import JsonRpcRequestDto
        from eidos_runtime.runtime.loop import RuntimeEngine
        from eidos_runtime.runtime.event_projector import EventProjector
        from eidos_runtime.runtime.supervisor import RunSupervisor
        from eidos_runtime.runtime.loop_guard import LoopGuard
        from eidos_runtime.sandbox.seatbelt import SeatbeltProfile
        from eidos_runtime.tools.workspace import ToolExecutor

        self.assertTrue(all((
            SessionStore,
            ContextBuilder,
            ContextCompactor,
            ModelResponse,
            JsonRpcRequestDto,
            RuntimeEngine,
            EventProjector,
            RunSupervisor,
            LoopGuard,
            SeatbeltProfile,
            ToolExecutor,
        )))


if __name__ == "__main__":
    unittest.main()

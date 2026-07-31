from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.model.pydantic_ai_client import (  # noqa: E402
    ModelClientFactory,
    ModelFactoryCloseError,
    ModelFactoryState,
    _ClientEntry,
)
from eidos_runtime.protocol.server import RuntimeServer  # noqa: E402
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel  # noqa: E402


class _ClosingClient:
    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        if self.close_calls <= self.failures:
            raise RuntimeError("close failed")


class ModelFactoryLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.kernel = RuntimeAsyncKernel()
        self.kernel.start()

    def tearDown(self) -> None:
        self.kernel.close()

    def test_factory_close_failure_keeps_failed_resources_visible(self) -> None:
        factory = ModelClientFactory(
            "sk-valid-key-for-tests", async_kernel=self.kernel
        )
        client = _ClosingClient(failures=1)
        factory._clients[("deepseek", "deepseek-v4-flash")] = _ClientEntry(client)

        with self.assertRaises(ModelFactoryCloseError):
            factory.close()

        self.assertEqual(factory.state, ModelFactoryState.FAILED)
        self.assertIn(
            ("deepseek", "deepseek-v4-flash"), factory._clients
        )

    def test_factory_close_failure_is_retryable(self) -> None:
        factory = ModelClientFactory(
            "sk-valid-key-for-tests", async_kernel=self.kernel
        )
        client = _ClosingClient(failures=1)
        factory._clients[("deepseek", "deepseek-v4-flash")] = _ClientEntry(client)

        with self.assertRaises(ModelFactoryCloseError):
            factory.close()
        factory.close()

        self.assertEqual(factory.state, ModelFactoryState.CLOSED)
        self.assertEqual(factory._clients, {})
        self.assertEqual(client.close_calls, 2)

    def test_reconfiguration_does_not_commit_api_key_before_swap_success(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-model-swap-") as root:
            data = Path(root) / "data"
            data.mkdir(mode=0o700)
            server = RuntimeServer(io.StringIO(), data)
            server.store.initialize()
            server.model_config.initialize()
            server.initialized = True
            server.async_kernel = self.kernel
            server.model_config.save_api_key("sk-existing-key-for-tests")
            previous = ModelClientFactory(
                "sk-existing-key-for-tests", async_kernel=self.kernel
            )
            server.model_factory = previous

            with (
                patch.object(
                    previous,
                    "close",
                    side_effect=ModelFactoryCloseError(
                        "MODEL_RECONFIGURATION_FAILED"
                    ),
                ),
                patch.object(
                    server.model_config,
                    "save_api_key",
                    wraps=server.model_config.save_api_key,
                ) as save,
            ):
                server.configure_model(
                    "client-config",
                    {"apiKey": "sk-replacement-key-for-tests"},
                )

            self.assertFalse(save.called)
            self.assertEqual(
                server.model_config.api_key(), "sk-existing-key-for-tests"
            )
            previous.close = lambda: None  # type: ignore[method-assign]
            server.model_factory = None
            server.close()

    def test_config_commit_failure_keeps_candidate_factory_active(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-model-swap-") as root:
            data = Path(root) / "data"
            data.mkdir(mode=0o700)
            server = RuntimeServer(io.StringIO(), data)
            server.store.initialize()
            server.model_config.initialize()
            server.initialized = True
            server.async_kernel = self.kernel
            previous = ModelClientFactory(
                "sk-existing-key-for-tests", async_kernel=self.kernel
            )
            server.model_factory = previous

            with patch.object(
                server.model_config,
                "save_api_key",
                side_effect=OSError("commit failed"),
            ):
                server.configure_model(
                    "client-config",
                    {"apiKey": "sk-replacement-key-for-tests"},
                )

            self.assertIsNot(server.model_factory, previous)
            self.assertEqual(
                json.loads(server.output.getvalue().splitlines()[-1])[
                    "error"
                ]["data"]["code"],
                "MODEL_CONFIG_COMMIT_FAILED",
            )
            server.close()


if __name__ == "__main__":
    unittest.main()

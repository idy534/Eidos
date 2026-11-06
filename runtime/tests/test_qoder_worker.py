"""Exercise the real worker and process driver with a deterministic SDK double."""
from __future__ import annotations

from pathlib import Path
import shutil
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from eidos_runtime.infrastructure.qoder_model_driver import BOOTSTRAP, QoderModelDriver
from eidos_runtime.model.client import ModelRequestError
from eidos_runtime.model.qoder_protocol import WorkerRequest, WorkerTool, WorkerResult
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
FAKE_SDK = '''
import asyncio
from dataclasses import dataclass
class QoderAgentOptions:
 def __init__(self, **values): self.__dict__.update(values)
class HookMatcher:
 def __init__(self, hooks): self.hooks=hooks
class PermissionResultDeny:
 def __init__(self, **values): pass
class AssistantMessage:
 def __init__(self, content): self.content=content
class StreamEvent:
 def __init__(self, event): self.event=event
class TextBlock:
 def __init__(self, text): self.text=text
class ToolUseBlock:
 def __init__(self, id, name, input): self.id,self.name,self.input=id,name,input
@dataclass
class ResultMessage:
 is_error: bool=False
 num_turns: int=1
 stop_reason: str='end_turn'
 terminal_reason: str='completed'
 usage: object=None
 total_credits: float=0.25
 uuid: str='fixture'
 session_id: str='session'
 errors: object=None
 subtype: str='success'
def qodercli_auth(): return object()
def tool(name, description, schema):
 def wrap(handler): return type('Tool',(),{'name':name,'handler':staticmethod(handler)})()
 return wrap
def create_sdk_mcp_server(name, tools): return tools
class QoderSDKClient:
 def __init__(self, options): self.options=options
 async def connect(self): pass
 async def disconnect(self): pass
 async def get_available_models(self): return [{'value':'fixture','displayName':'Fixture','isEnabled':True,'isVl':True,'maxOutputTokens':32000,'defaultContextWindow':200000,'context_config':{'200K':{'token_count':200000,'is_default':True},'1M':{'token_count':1000000}},'thinking_config':{'enabled':{'efforts':{'low':{},'high':{'is_default':True}},'is_default':True}}}]
 async def query(self, prompt): self.case=prompt
 async def receive_response(self):
  assert self.options.tools == [] and self.options.setting_sources == []
  assert self.options.skills == [] and self.options.plugins == []
  assert self.options.strict_mcp_config and not self.options.settings['general']['fileCheckpointing']['enabled']
  await self.options.resolve_model({'purpose':'main','availableModels':[{'value':'fixture','isEnabled':True}]})
  if self.case == 'wait': await asyncio.sleep(60)
  if self.case == 'secondary':
   try: await self.options.resolve_model({'purpose':'compaction'})
   except Exception: pass
  if self.case == 'handler':
   try: await self.options.mcp_servers['eidos'][0].handler({})
   except Exception: pass
  if self.case == 'closed_callback':
   async def late():
    try: await asyncio.sleep(60)
    except asyncio.CancelledError: raise RuntimeError('closed transport fixture secret')
   asyncio.create_task(late())
   await asyncio.sleep(0)
  if self.case in ('parallel','duplicate','unknown','truncated'):
   names=['mcp__eidos__t000','mcp__eidos__t001']
   for i,name in enumerate(names):
    if self.case == 'unknown': name='run_shell'
    call_id='a' if self.case == 'duplicate' else str(i)
    yield AssistantMessage([ToolUseBlock(call_id,name,{'key':str(i)})])
    await self.options.hooks['PreToolUse'][0].hooks[0]({'tool_name':name},call_id,{})
   yield ResultMessage(stop_reason='length' if self.case=='truncated' else 'tool_use',terminal_reason='hook_stopped')
  else:
   yield StreamEvent({'type':'content_block_delta','delta':{'type':'text_delta','text':'hello'}})
   yield AssistantMessage([TextBlock('hello')])
   if self.case != 'missing_result': yield ResultMessage()
'''


@pytest.fixture
def driver(tmp_path):
    package = tmp_path / "lib/eidos_qoder_worker"
    shutil.copytree(ROOT / "resources/runtime-dependencies/qoder/src/eidos_qoder_worker", package)
    shutil.copy2(ROOT / "runtime/eidos_runtime/model/qoder_protocol.py", package / "protocol.py")
    (tmp_path / "lib/qoder_agent_sdk.py").write_text(FAKE_SDK)
    binding = SimpleNamespace(config_directory=tmp_path / "private", command=[sys.executable, "-I", "-c", BOOTSTRAP,
        str(package.parent), "unused-cli", str(tmp_path / "private")])
    kernel = RuntimeAsyncKernel()
    kernel.start()
    driver = QoderModelDriver(binding)
    yield kernel, driver
    kernel.call(driver.close)
    kernel.close()


def request(case):
    return WorkerRequest(request_id=case, operation="sample", model_id="fixture", prompt=case,
        tools=tuple(WorkerTool(name=f"t{i:03d}", description="fixture", schema={"type":"object"}) for i in range(2)))


def test_worker_collects_entire_ordered_batch_without_executing_tools(driver):
    kernel, worker = driver
    result = kernel.call(worker.request, request("parallel"), threading.Event(), lambda _text: None, 10)
    assert isinstance(result.payload, WorkerResult)
    assert [call.id for call in result.payload.calls] == ["0", "1"]
    assert result.payload.credits == 0.25
    assert result.payload.input_tokens is None


@pytest.mark.parametrize("case", ["duplicate", "unknown", "truncated", "handler", "secondary", "missing_result", "closed_callback"])
def test_worker_fails_closed_on_boundary_violations(driver, case):
    kernel, worker = driver
    with pytest.raises(ModelRequestError) as caught:
        kernel.call(worker.request, request(case), threading.Event(), lambda _text: None, 10)
    assert caught.value.failure.code == "protocol_error"
    assert not caught.value.failure.retryable
    assert "secret" not in str(caught.value)
    assert worker._process is None


def test_same_lease_uses_fresh_sdk_sessions_and_retains_one_worker(driver):
    kernel, worker = driver
    text = []
    first = kernel.call(worker.request, request("text"), threading.Event(), text.append, 10)
    pid = worker._process.pid
    second = kernel.call(worker.request, request("text"), threading.Event(), text.append, 10)
    assert worker._process.pid == pid
    assert text == ["hello", "hello"]
    assert isinstance(first.payload, WorkerResult) and isinstance(second.payload, WorkerResult)


def test_cancel_closes_worker_and_prevents_late_success(driver):
    kernel, worker = driver
    cancel = threading.Event()
    timer = threading.Timer(0.4, cancel.set)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(ModelRequestError) as caught:
            kernel.call(worker.request, request("wait"), cancel, lambda _text: pytest.fail("late text"), 15)
        assert caught.value.failure.code == "sampling_canceled"
        assert worker._process is None and time.monotonic() - started < 4
    finally:
        timer.cancel()
        timer.join()


def test_timeout_closes_worker(driver):
    kernel, worker = driver
    with pytest.raises(ModelRequestError) as caught:
        kernel.call(worker.request, request("wait"), threading.Event(), lambda _text: None, 0.4)
    assert caught.value.failure.code == "provider_timeout" and worker._process is None


def test_nonprotocol_stdout_is_rejected(tmp_path):
    kernel = RuntimeAsyncKernel()
    kernel.start()
    worker = QoderModelDriver(SimpleNamespace(config_directory=tmp_path, command=[sys.executable, "-c", "print('diagnostic text')"]))
    try:
        with pytest.raises(ModelRequestError) as caught:
            kernel.call(worker.request, request("x"), threading.Event(), lambda _text: None, 3)
        assert caught.value.failure.code == "protocol_error"
        assert worker._process is None
    finally:
        kernel.call(worker.close)
        kernel.close()


def test_permission_error_only_counts_as_clean_after_group_disappears(tmp_path, monkeypatch):
    worker = QoderModelDriver(SimpleNamespace(config_directory=tmp_path))
    class Exited:
        pid = 999999
        returncode = 0
        closed = False
        async def wait(self):
            return 0
        async def aclose(self):
            self.closed = True
    process = Exited()
    worker._process = process
    def signal_group(pid, signum):
        if signum:
            raise PermissionError("already exited signal race")
        raise ProcessLookupError()
    monkeypatch.setattr("eidos_runtime.infrastructure.qoder_model_driver.os.killpg", signal_group)
    kernel = RuntimeAsyncKernel()
    kernel.start()
    try:
        kernel.call(worker.close)
        assert worker._process is None and process.closed
    finally:
        kernel.close()


def test_worker_error_keeps_credits_without_returning_provider_error_text(driver):
    kernel, worker = driver
    with pytest.raises(ModelRequestError) as caught:
        kernel.call(worker.request, request("truncated"), threading.Event(), lambda _text: None, 10)
    assert caught.value.failure.code == "protocol_error"
    assert caught.value.failure.usage.credits_usage.amount == 0.25


def test_sdk_catalog_preserves_real_context_reasoning_and_vision_metadata(driver):
    kernel, worker = driver
    result = kernel.call(worker.request, WorkerRequest(request_id="catalog", operation="catalog"),
                         threading.Event(), lambda _text: None, 10)
    model = result.payload.models[0]
    assert model.context_windows == (200000, 1000000)
    assert model.default_context_window == 200000 and model.max_output_tokens == 32000
    assert model.supports_images is True
    assert model.reasoning_selections == ("low", "high") and model.default_reasoning == "high"

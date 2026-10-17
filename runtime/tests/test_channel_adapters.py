from __future__ import annotations

import asyncio
import json

import anyio
import httpx2 as httpx
import pytest

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.channels import ChannelConfig, ChannelConnection, ChannelError, ChannelMessage
from eidos_runtime.infrastructure.channel_adapter import ChannelSendError
from eidos_runtime.infrastructure.feishu import FeishuAdapter, normalize_feishu
from eidos_runtime.infrastructure.wecom import WeComAdapter, normalize_wecom
from eidos_runtime.persistence.channels import ChannelRepository

pytestmark = pytest.mark.integration


def connection(channel="feishu"):
    return ChannelConnection(channel=channel, id=channel, revision=1, secret_ref="reference",
        config=ChannelConfig(enabled=True, client_id="app", model_id="model"))


def feishu_payload(*, chat="group", mention="bot", sender="user", kind="text"):
    return {"schema": "2.0", "header": {"event_type": "im.message.receive_v1", "tenant_key": "tenant"},
        "event": {"sender": {"sender_type": sender, "sender_id": {"open_id": "user"}},
            "message": {"message_id": "msg", "chat_id": "chat", "chat_type": chat, "message_type": kind,
                "content": json.dumps({"text": "@_user_1 检查文件"}),
                "mentions": [{"key": "@_user_1", "id": {"open_id": mention}}]}}}


def wecom_frame(chat="group"):
    return {"cmd": "aibot_msg_callback", "headers": {"req_id": "request"},
        "body": {"msgid": "msg", "aibotid": "app", "chattype": chat, "chatid": "chat",
                 "from": {"userid": "user"}, "msgtype": "text", "text": {"content": "检查文件"}}}


@pytest.mark.parametrize("chat", ["p2p", "group"])
def test_feishu_normalization_uses_real_bot_identity_and_sender(chat):
    from eidos_runtime.application.channels import accepts_message
    incoming = normalize_feishu(feishu_payload(chat=chat), "bot", "app")
    assert incoming.text == "检查文件" and incoming.sender_id == "user"
    assert incoming.corp_id == "tenant" and incoming.mentioned
    assert accepts_message(connection(), incoming)
    other = normalize_feishu(feishu_payload(chat=chat, mention="other-bot"), "bot", "app")
    assert other.mentioned is False
    assert accepts_message(connection(), other) == (chat == "p2p")
    assert normalize_feishu(feishu_payload(sender="app"), "bot", "app") is None
    assert normalize_feishu(feishu_payload(chat="unknown"), "bot", "app") is None
    assert normalize_feishu(feishu_payload(kind="image"), "bot", "app").supported is False


@pytest.mark.parametrize("chat", ["single", "group"])
def test_wecom_uses_platform_callback_routing_without_inventing_a_mention_field(chat):
    incoming = normalize_wecom(wecom_frame(chat), "app")
    assert incoming.sender_id == "user" and incoming.robot_code == "app"
    assert incoming.conversation_id == ("user" if chat == "single" else "chat")
    assert incoming.mentioned == (chat == "group")
    assert normalize_wecom(wecom_frame(), "different-bot") is None
    assert normalize_wecom({"cmd": "aibot_event_callback", "body": wecom_frame()["body"]}, "app") is None


def test_multiple_provider_configs_dedup_deliveries_and_legacy_records_are_isolated(tmp_path):
    store = SessionStore(tmp_path / "data")
    store.initialize()
    repository = ChannelRepository(store.database)
    incoming = ChannelMessage(msg_id="same", corp_id="tenant", robot_code="app", conversation_id="chat",
        conversation_type="2", sender_id="user", mentioned=True, text="hello")
    try:
        identifiers = []
        for channel in ("dingtalk", "feishu", "wecom"):
            value = connection(channel)
            repository.save(value, 0)
            identifier = repository.receive(value, incoming)
            assert repository.receive(value, incoming) == identifier
            identifiers.append(identifier)
            repository.reject(identifier, channel)
        assert len(set(identifiers)) == 3
        for channel in ("dingtalk", "feishu", "wecom"):
            assert repository.connection(channel).channel == channel
            assert [d.text for d in repository.deliveries(channel)] == [channel]
        legacy = connection("dingtalk").model_dump()
        legacy.pop("channel")
        store.connection.execute("UPDATE channel_connections SET connection_json=? WHERE id='dingtalk'", (json.dumps(legacy),))
        assert repository.connection().channel == "dingtalk"
        repository.save(connection("feishu").model_copy(update={"revision": 2}), 1)
        assert repository.connection().revision == repository.connection("wecom").revision == 1
        with pytest.raises(ChannelError):
            repository.save(connection("wecom").model_copy(update={"id": "feishu", "revision": 2}), 1)
        with pytest.raises(ChannelError):
            repository.require_current(connection("feishu"))
        directory = store.data_directory
        store.close()
        store = SessionStore(directory)
        store.initialize()
        repository = ChannelRepository(store.database)
        assert repository.connection().channel == "dingtalk"
        assert repository.connection("feishu").revision == 2
        assert len(repository.deliveries("wecom")) == 1
    finally:
        store.close()


@pytest.mark.parametrize("response,uncertain,retry", [(httpx.Response(429), False, True), (httpx.Response(500), True, False),
    (httpx.Response(200, json={"code": 230002}), False, False), (httpx.Response(200, json={"code": 0}), True, False),
    (httpx.Response(200, json={"code": False}), True, False)])
def test_feishu_reply_target_receipt_and_uncertain_delivery(monkeypatch, response, uncertain, retry):
    calls = []
    def handle(request):
        calls.append(request)
        if request.url.path.endswith("internal"):
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "private-token", "expire": 7200})
        assert request.url.path == "/open-apis/im/v1/messages/msg/reply"
        assert request.headers["Authorization"] == "Bearer private-token"
        assert json.loads(json.loads(request.content)["content"])["text"] == "answer"
        return response
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    incoming = normalize_feishu(feishu_payload(), "bot", "app")
    with pytest.raises(ChannelSendError) as raised:
        anyio.run(FeishuAdapter().send, connection(), "secret", incoming, "answer")
    assert (raised.value.uncertain, raised.value.retry) == (uncertain, retry)
    assert "private" not in str(raised.value)


def test_feishu_send_requires_a_message_receipt_and_keeps_token_only_in_memory(monkeypatch):
    calls = []
    def handle(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"code": 0, "tenant_access_token": "token", "expire": 7200}) if request.url.path.endswith("internal") else httpx.Response(200, json={"code": 0, "data": {"message_id": "receipt"}})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    async def exercise():
        adapter = FeishuAdapter()
        incoming = normalize_feishu(feishu_payload(), "bot", "app")
        await adapter.send(connection(), "secret", incoming, "one")
        await adapter.send(connection(), "secret", incoming, "two")
    anyio.run(exercise)
    assert len(calls) == 3


class Socket:
    def __init__(self):
        self.frames = asyncio.Queue()
        self.sent = []
        self.closed = False
        self.open = True
    async def __aenter__(self): return self
    async def __aexit__(self, *args):
        self.closed = True
        self.open = False
    def __aiter__(self): return self
    async def __anext__(self): return await self.frames.get()
    async def send(self, payload): self.sent.append(payload)
    async def close(self, *args, **kwargs): self.closed = True


@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("parts", [1, 2])
def test_feishu_official_codec_acks_after_persistence_and_cancels_without_extra_loop(monkeypatch, caplog, failure, parts):
    async def exercise():
        from eidos_runtime.infrastructure.feishu_stream import FeishuStream
        from lark_channel.ws.pb.pbbp2_pb2 import Frame
        import eidos_runtime.infrastructure.feishu_stream as bridge
        monkeypatch.setattr(asyncio, "new_event_loop", lambda: pytest.fail("must reuse the kernel"))
        socket = Socket()
        options = []
        monkeypatch.setattr(bridge.websockets, "connect", lambda url, **kwargs: (options.append(kwargs) or socket))
        observed = []
        async def request(*args):
            return {"code": 0, "data": {"URL": "wss://msg-frontier.feishu.cn/ws?service_id=1&ticket=private-ticket"}}
        def receive(payload):
            assert not socket.sent or all(json.loads(Frame.FromString(v).payload).get("code") != 200 for v in socket.sent if Frame.FromString(v).method == 1)
            observed.append(payload)
            if failure:
                raise RuntimeError("private-storage-error")
        stream = FeishuStream("app", "private-secret", receive, request)
        frame = Frame(SeqID=0, LogID=0, method=1, service=1, payload=json.dumps(feishu_payload()).encode())
        for key, value in {"type": "event", "message_id": "event", "trace_id": "trace", "sum": "1", "seq": "0"}.items():
            header = frame.headers.add()
            header.key = key
            header.value = value
        payload = frame.payload
        for seq in range(parts):
            packet = Frame()
            packet.CopyFrom(frame)
            for header in packet.headers:
                if header.key == "sum":
                    header.value = str(parts)
                if header.key == "seq":
                    header.value = str(seq)
            packet.payload = payload[seq * len(payload) // parts:(seq + 1) * len(payload) // parts]
            await socket.frames.put(packet.SerializeToString())
        async with anyio.create_task_group() as group:
            group.start_soon(stream.listen)
            with anyio.fail_after(2):
                while not observed or not any(Frame.FromString(v).method == 1 for v in socket.sent):
                    await anyio.sleep(0.01)
            ack = next(Frame.FromString(v) for v in socket.sent if Frame.FromString(v).method == 1)
            assert json.loads(ack.payload)["code"] == (500 if failure else 200)
            assert stream.connected
            group.cancel_scope.cancel()
        assert not stream.connected and socket.closed
        assert options[0]["max_size"] == 128 * 1024
    anyio.run(exercise)
    assert "private-" not in caplog.text


@pytest.mark.parametrize("reject", [False, True, "timeout", "invalid"])
def test_wecom_official_auth_callback_send_ack_and_owned_shutdown(monkeypatch, reject, caplog):
    import eidos_runtime.infrastructure.wecom as module
    async def exercise():
        socket = Socket()
        async def send(raw):
            frame = json.loads(raw)
            socket.sent.append(frame)
            if frame["cmd"] == "aibot_send_msg" and reject == "timeout":
                return
            if frame["cmd"] in {"aibot_subscribe", "aibot_send_msg"}:
                await socket.frames.put(json.dumps({"headers": frame["headers"], "errcode": (None if reject == "invalid" else 400) if reject and frame["cmd"] == "aibot_send_msg" else 0, "errmsg": "private-error"}))
                if frame["cmd"] == "aibot_subscribe":
                    await socket.frames.put(json.dumps(wecom_frame()))
        socket.send = send
        monkeypatch.setattr(module.websockets, "connect", lambda *args, **kwargs: socket)
        adapter = WeComAdapter()
        received = []
        async with anyio.create_task_group() as group:
            group.start_soon(adapter.listen, connection("wecom"), "private-secret", lambda _, msg: received.append(msg))
            with anyio.fail_after(2):
                while not received:
                    await anyio.sleep(0.01)
                adapter.client._reply_ack_timeout = 0.02
                if reject:
                    with pytest.raises(ChannelSendError) as raised:
                        await adapter.send(connection("wecom"), "private-secret", received[0], "answer")
                    assert raised.value.uncertain == (reject in {"timeout", "invalid"})
                    assert "private" not in str(raised.value)
                else:
                    await adapter.send(connection("wecom"), "private-secret", received[0], "answer")
            client = adapter.client
            assert adapter.connected
            group.cancel_scope.cancel()
        assert not adapter.connected and socket.closed
        assert not client.reply_tasks and not client._pending_acks
        sent = next(f for f in socket.sent if f["cmd"] == "aibot_send_msg")
        assert sent["body"]["chatid"] == "chat"
        assert sent["body"]["markdown"]["content"] == "answer"
    anyio.run(exercise)
    assert "private-" not in caplog.text


def test_wecom_disconnected_send_has_no_side_effect_and_is_retryable():
    incoming = normalize_wecom(wecom_frame(), "app")
    with pytest.raises(ChannelSendError) as raised:
        anyio.run(WeComAdapter().send, connection("wecom"), "secret", incoming, "answer")
    assert raised.value.retry and not raised.value.uncertain


@pytest.mark.parametrize("channel", ["feishu", "wecom"])
def test_provider_failure_does_not_stop_other_channel_connections(monkeypatch, channel):
    from eidos_runtime.runtime.channel_service import ChannelService
    from types import SimpleNamespace
    async def exercise():
        repository = SimpleNamespace(recover=lambda: None, connection=lambda kind="dingtalk": connection(kind))
        app = SimpleNamespace(repository=repository, secrets=SimpleNamespace(read=lambda _: "secret"), receive=lambda *_: None)
        service = ChannelService(app, lambda: True, lambda: None, lambda _: False)
        ready = anyio.Event()
        class Healthy:
            connected = True
            async def listen(self, *_):
                ready.set()
                await anyio.sleep_forever()
        class Broken:
            connected = False
            async def listen(self, *_):
                raise ChannelError("private-platform-error")
        service.adapter = Healthy()
        service.adapters[channel] = Broken()
        other = "wecom" if channel == "feishu" else "feishu"
        service.adapters[other] = Healthy()
        async def work():
            await anyio.sleep_forever()
        monkeypatch.setattr(service, "_work", work)
        async with anyio.create_task_group() as group:
            group.start_soon(service.run)
            with anyio.fail_after(2):
                await ready.wait()
                while service.states.get(channel) != "error" or service.states.get("dingtalk") != "connected":
                    await anyio.sleep(0.01)
            assert service.states.get(other) == "connected"
            group.cancel_scope.cancel()
        assert all(value == "disabled" for value in service.states.values())
    anyio.run(exercise)


def test_wecom_cancellation_awaits_pending_sdk_replies(monkeypatch):
    import eidos_runtime.infrastructure.wecom as module
    async def exercise():
        socket = Socket()
        sent = anyio.Event()
        async def send(raw):
            frame = json.loads(raw)
            if frame["cmd"] == "aibot_subscribe":
                await socket.frames.put(json.dumps({"headers": frame["headers"], "errcode": 0}))
            elif frame["cmd"] == "aibot_send_msg":
                sent.set()
        socket.send = send
        monkeypatch.setattr(module.websockets, "connect", lambda *args, **kwargs: socket)
        adapter = WeComAdapter()
        async with anyio.create_task_group() as group:
            group.start_soon(adapter.listen, connection("wecom"), "secret", lambda *_: None)
            with anyio.fail_after(2):
                while not adapter.connected:
                    await anyio.sleep(0.01)
                client = adapter.client
                group.start_soon(adapter.send, connection("wecom"), "secret", normalize_wecom(wecom_frame(), "app"), "answer")
                await sent.wait()
            group.cancel_scope.cancel()
        assert not client.reply_tasks and not client._pending_acks and not client._reply_queues
        assert socket.closed
    anyio.run(exercise)


@pytest.mark.parametrize("channel", ["feishu", "wecom"])
def test_managed_provider_callback_creates_run_and_routes_final_delivery(tmp_path, monkeypatch, channel):
    import io
    import time
    from eidos_runtime.protocol.channels import ChannelSaveRequest
    from eidos_runtime.protocol.server import RuntimeServer
    from eidos_runtime.model.client import ScriptedModel, ModelResponse
    from eidos_runtime.model.config import DEFAULT_MODEL_ID
    adapter_type = FeishuAdapter if channel == "feishu" else WeComAdapter
    sent = []
    async def listen(adapter, snapshot, secret, receive):
        incoming = normalize_feishu(feishu_payload(), "bot", "app") if channel == "feishu" else normalize_wecom(wecom_frame(), "app")
        receive(snapshot, incoming)
        receive(snapshot, incoming)
        await anyio.sleep_forever()
    async def send(adapter, snapshot, secret, incoming, text):
        assert snapshot.channel == channel
        sent.append((incoming.sender_id, text))
    monkeypatch.setattr(adapter_type, "listen", listen)
    monkeypatch.setattr(adapter_type, "send", send)
    output = io.StringIO()
    server = RuntimeServer(output, tmp_path / "data", model=ScriptedModel([ModelResponse(text="渠道回复")]))
    try:
        server.handle({"jsonrpc": "2.0", "id": "client-init", "method": "initialize", "params": {
            "protocolVersion": 1, "client": {"name": "test", "version": "1"}}})
        assert server.initialized, output.getvalue()
        model = server.model_config.create(provider_id="deepseek", model_id=DEFAULT_MODEL_ID, api_key="test-key")
        server.channel_service.application.save(ChannelSaveRequest(channel=channel, expected_revision=0, client_secret="channel-secret",
            config=ChannelConfig(enabled=True, client_id="app", model_id=model.id)))
        deadline = time.monotonic() + 10
        repository = server.channel_service.application.repository
        while time.monotonic() < deadline:
            replies = repository.deliveries(channel)
            if replies and replies[0].status == "sent":
                break
            time.sleep(0.02)
        assert sent == [("user", "渠道回复")]
        assert repository.deliveries(channel)[0].status == "sent"
        assert repository.deliveries("dingtalk") == []
        row = server.store.connection.execute("SELECT run_id FROM channel_inbound").fetchone()
        assert repository.origin(row[0]).channel == channel
        assert server.store.connection.execute("SELECT count(*) FROM channel_inbound").fetchone()[0] == 1
    finally:
        server.close()
    assert server.supervisor.resources.active_resources() == ()


def test_wecom_markdown_reply_chunks_are_utf8_bounded(tmp_path):
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        repository = ChannelRepository(store.database)
        snapshot = connection("wecom")
        repository.save(snapshot, 0)
        identifier = repository.receive(snapshot, normalize_wecom(wecom_frame(), "app"))
        repository.reject(identifier, "文" * 20_000)
        replies = repository.deliveries("wecom")
        assert 1 < len(replies) <= 9
        assert all(0 < len(reply.text.encode()) <= 3000 for reply in replies)
        assert "完整回答" in replies[0].text
    finally:
        store.close()

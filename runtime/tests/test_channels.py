from __future__ import annotations

import io
import json
import logging
import shlex
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

import anyio
import httpx2 as httpx
import pytest
from pydantic import ValidationError

from eidos_runtime.application.channels import accepts_message
from eidos_runtime.db.channel_migration import migrate_channels
from eidos_runtime.db.repositories.runs import RunRepository
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.channels import ChannelConfig, ChannelConnection, ChannelError, ChannelMessage
from eidos_runtime.infrastructure.channel_secrets import ChannelSecrets
from eidos_runtime.infrastructure.dingtalk import DingTalkAdapter, DingTalkSendError
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.model.config import DEFAULT_MODEL_ID
from eidos_runtime.persistence.channels import ChannelRepository, stable_id
from eidos_runtime.protocol.channels import ChannelSaveRequest
from eidos_runtime.protocol.server import RuntimeServer

pytestmark = pytest.mark.integration


def configuration(**changes):
    return ChannelConfig(client_id="app", model_id=DEFAULT_MODEL_ID, **changes)


def message(**changes):
    values = dict(msg_id="message", corp_id="corp", robot_code="robot", conversation_id="group",
                  conversation_type="2", sender_id="user", mentioned=True, text="检查状态。")
    return ChannelMessage(**(values | changes))


@pytest.fixture
def channels(tmp_path):
    store = SessionStore(tmp_path / "data")
    store.initialize()
    repository = ChannelRepository(store.database)
    connection = ChannelConnection(id=str(uuid4()), revision=1, config=configuration(enabled=True), secret_ref=str(uuid4()))
    repository.save(connection, 0)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = store.create_session(str(workspace))["id"]
    yield store, repository, connection, session
    store.close()


@pytest.mark.parametrize("changes", [dict(corp_id="other"), dict(robot_code="other"), dict(sender_id="other"),
    dict(conversation_id="other"), dict(conversation_type="1", mentioned=False)])
def test_direct_and_mentioned_group_messages_need_no_local_allowlist(changes):
    connection = ChannelConnection(id="connection", revision=1, config=configuration(enabled=True), secret_ref="ref")
    assert accepts_message(connection, message())
    assert accepts_message(connection, message(**changes))


def test_disabled_channels_and_unmentioned_group_messages_are_ignored(channels):
    from eidos_runtime.application.channels import ChannelApplication
    _, repository, connection, _ = channels
    application = ChannelApplication(repository, None, None, None, None)
    application.receive(connection, message(mentioned=False))
    assert repository.pending() == []
    disabled = connection.model_copy(update={"config": configuration()})
    assert not accepts_message(disabled, message())
    assert not accepts_message(disabled, message(conversation_type="1", mentioned=False))
    application.receive(connection, message(conversation_type="1", mentioned=False, sender_id="new-user"))
    assert len(repository.pending()) == 1


def test_strict_config_rejects_invalid_fields_and_retired_options():
    for changes in ({"approval_mode": "unknown"}, {"enabled": 1}, {"client_id": " "}, {"robot_code": "robot"},
                    {"corp_id": "corp"}, {"allowed_user_ids": []}, {"allowed_conversation_ids": []}, {"allow_direct_messages": False}):
        with pytest.raises(ValidationError):
            ChannelConfig.model_validate(configuration().model_dump() | changes)


def test_full_access_save_requires_confirmation_persists_it_and_clears_on_downgrade(channels):
    from eidos_runtime.application.channels import ChannelApplication
    store, repository, old, _ = channels
    secrets = ChannelSecrets(store.data_directory)
    application = ChannelApplication(repository, secrets, None, None, None)
    request = ChannelSaveRequest(config=configuration(approval_mode="full_access"), expected_revision=1, client_secret="channel-secret")
    with pytest.raises(ChannelError, match="由用户确认"):
        application.save(request)
    assert repository.connection() == old
    application.save(request.model_copy(update={"full_access_confirmation": "full-access-v1"}))
    confirmed = repository.connection()
    assert confirmed.config.approval_mode == "full_access"
    assert confirmed.full_access_confirmation == "full-access-v1"
    assert type(confirmed).model_validate_json(confirmed.model_dump_json()) == confirmed
    raw = confirmed.model_dump()
    del raw["full_access_confirmation"]
    with pytest.raises(ValidationError):
        ChannelConnection.model_validate(raw)
    application.save(ChannelSaveRequest(config=confirmed.config, expected_revision=2))
    assert repository.connection().full_access_confirmation == "full-access-v1"
    with pytest.raises(ChannelError, match="不一致"):
        application.save(ChannelSaveRequest(config=configuration(), expected_revision=3, full_access_confirmation="full-access-v1"))
    application.save(ChannelSaveRequest(config=configuration(), expected_revision=3))
    restored = repository.connection()
    assert restored.config.approval_mode == "manual" and restored.full_access_confirmation is None
    assert "full_access_confirmation" not in restored.model_dump()
    with pytest.raises(ChannelError, match="由用户确认"):
        application.save(ChannelSaveRequest(config=configuration(approval_mode="full_access"), expected_revision=4))


def test_inbound_dedup_and_atomic_run_origin(channels):
    store, repository, connection, session = channels
    identifier = repository.receive(connection, message())
    assert repository.receive(connection, message()) == identifier
    assert repository.pending() == [identifier]
    run_id = repository.accept(identifier, session, lambda transaction: str(RunRepository(store.database).create_run(
        session, message().text, queued=True, transaction_connection=transaction)[0]["id"]))
    run = store.read_run(run_id)
    item = store.connection.execute("SELECT id FROM items WHERE run_id=?", (run_id,)).fetchone()
    assert repository.inbound(identifier).run_id == run["id"]
    assert repository.origin(run["id"]).sender_id == "user"
    assert store.approval_user_evidence(run["id"]) == ()
    assert store.connection.execute("SELECT eligible FROM memory_source_items WHERE item_id=?", (item["id"],)).fetchone()[0] == 0
    assert store.read_run(run["id"])["channelOrigin"]["messageId"] == identifier
    with pytest.raises(ChannelError):
        repository.accept(identifier, session, lambda _: pytest.fail("duplicate Run"))


@pytest.mark.parametrize("failure", [RuntimeError, sqlite3.OperationalError])
def test_run_and_origin_roll_back_together(channels, failure):
    store, repository, connection, session = channels
    identifier = repository.receive(connection, message())
    def fail(transaction):
        RunRepository(store.database).create_run(session, "request", queued=True, transaction_connection=transaction)
        raise failure("injected failure")
    with pytest.raises(failure):
        repository.accept(identifier, session, fail)
    assert repository.inbound(identifier).status == "pending"
    assert store.connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
    assert store.connection.execute("SELECT count(*) FROM channel_run_origins").fetchone()[0] == 0


def test_disable_or_revision_change_prevents_late_admission(channels):
    store, repository, connection, session = channels
    identifier = repository.receive(connection, message())
    repository.save(connection.model_copy(update={"revision": 2, "config": connection.config.model_copy(update={"enabled": False})}), 1)
    with pytest.raises(ChannelError):
        repository.accept(identifier, session, lambda _: pytest.fail("disabled admission"))
    with pytest.raises(ChannelError):
        repository.save(connection, 1)
    with pytest.raises(ChannelError):
        repository.receive(connection, message(msg_id="late"))


def test_conversation_and_sender_bindings_preserve_context_without_mixing(channels):
    from types import SimpleNamespace
    from eidos_runtime.application.channels import ChannelApplication
    store, repository, connection, session = channels
    class Sessions:
        def create(self, request):
            return SimpleNamespace(id=store.create_session(store.read_session(session)["workspaceRoot"])["id"])
    class Runs:
        def start(self, request, *, channel_message_id):
            repository.accept(channel_message_id, request.session_id, lambda transaction: str(RunRepository(store.database).create_run(
                request.session_id, request.user_input, queued=True, transaction_connection=transaction)[0]["id"]))
            return SimpleNamespace(release_after_commit=lambda: None)
    application = ChannelApplication(repository, None, Sessions(), Runs(), None)
    identifiers = []
    for changes in ({"msg_id": "one"}, {"msg_id": "two"}, {"msg_id": "three", "sender_id": "other"},
                    {"msg_id": "four", "conversation_id": "second-group"}, {"msg_id": "five", "corp_id": "other-corp"}):
        incoming = message(**changes)
        application.receive(connection, incoming)
        identifiers.append(repository.receive(connection, incoming))
    application.process_pending()
    sessions = [repository.inbound(identifier).session_id for identifier in identifiers]
    assert sessions[0] == sessions[1]
    assert len(set(sessions)) == 4


def test_saved_legacy_config_and_inbox_snapshots_remain_readable_after_restart(channels):
    from types import SimpleNamespace
    from eidos_runtime.application.channels import ChannelApplication
    store, repository, connection, session = channels
    identifier = repository.receive(connection, message())
    route = stable_id(json.dumps(
        [connection.id, "app", "corp", "group", "user", None]))
    repository.bind(route, session)
    legacy = connection.model_dump()
    legacy["config"].update(robot_code="robot", corp_id="corp", allowed_user_ids=["someone-else"],
                            allowed_conversation_ids=[], allow_direct_messages=False)
    with store.database.transaction() as transaction:
        transaction.execute("UPDATE channel_connections SET connection_json=?", (json.dumps(legacy),))
        transaction.execute("UPDATE channel_inbound SET snapshot_json=? WHERE id=?", (json.dumps(legacy), identifier))
    directory = store.database.data_directory
    store.close()
    restored = SessionStore(directory)
    try:
        restored.initialize()
        recovered = ChannelRepository(restored.database)
        assert recovered.connection() == connection
        assert recovered.inbound(identifier).connection == connection
        class Runs:
            def start(self, request, *, channel_message_id):
                assert request.session_id == session
                recovered.accept(channel_message_id, session, lambda transaction: str(RunRepository(restored.database).create_run(
                    session, request.user_input, queued=True, transaction_connection=transaction)[0]["id"]))
                return SimpleNamespace(release_after_commit=lambda: None)
        sessions = SimpleNamespace(create=lambda _: pytest.fail("Existing Session must be reused"))
        application = ChannelApplication(recovered, None, sessions, Runs(), None)
        application.process_pending()
        assert recovered.inbound(identifier).status == "submitted"
        assert recovered.inbound(identifier).session_id == session
        recovered.save(connection.model_copy(update={"revision": 2}), 1)
        saved = json.loads(restored.connection.execute("SELECT connection_json FROM channel_connections").fetchone()[0])
        assert set(saved["config"]) == set(connection.config.model_dump())
        legacy["config"]["unknown_option"] = True
        with restored.database.transaction() as transaction:
            transaction.execute("UPDATE channel_connections SET connection_json=?", (json.dumps(legacy),))
        with pytest.raises(ValidationError):
            recovered.connection()
    finally:
        restored.close()


def test_pending_and_sending_records_survive_real_database_restart(channels):
    store, repository, connection, _ = channels
    pending = repository.receive(connection, message(msg_id="pending"))
    sending = repository.receive(connection, message(msg_id="sending"))
    repository.reject(sending, "reply")
    repository.claim_delivery()
    directory = store.database.data_directory
    store.close()
    restored = SessionStore(directory)
    try:
        restored.initialize()
        recovered = ChannelRepository(restored.database)
        recovered.recover()
        assert recovered.pending() == [pending]
        assert recovered.deliveries()[0].status == "unknown"
        assert recovered.receive(connection, message(msg_id="pending")) == pending
        assert recovered.pending() == [pending]
    finally:
        restored.close()


def test_wait_notice_final_reply_are_persisted_and_deduplicated(channels):
    store, repository, connection, session = channels
    identifier = repository.receive(connection, message())
    run_id = repository.accept(identifier, session, lambda transaction: str(RunRepository(store.database).create_run(
        session, "request", queued=True, transaction_connection=transaction)[0]["id"]))
    run = store.read_run(run_id)
    with store.database.transaction() as transaction:
        transaction.execute("UPDATE runs SET status='waiting_approval' WHERE id=?", (run["id"],))
    repository.collect_replies()
    repository.collect_replies()
    assert len(repository.deliveries()) == 1
    assert "本机" in repository.deliveries()[0].text
    with store.database.transaction() as transaction:
        transaction.execute("UPDATE runs SET status='canceled' WHERE id=?", (run["id"],))
    repository.collect_replies()
    repository.collect_replies()
    assert len(repository.deliveries()) == 2
    assert repository.deliveries()[0].text == "任务已取消。"


def test_delivery_claim_and_restart_never_blindly_resend(channels):
    _, repository, connection, _ = channels
    identifier = repository.receive(connection, message())
    repository.reject(identifier, "answer")
    delivery = repository.claim_delivery()
    assert delivery.status == "sending"
    assert repository.claim_delivery() is None
    repository.recover()
    assert repository.deliveries()[0].status == "unknown"
    assert repository.claim_delivery() is None


def test_rejection_after_submission_does_not_report_a_false_start_failure(channels):
    store, repository, connection, session = channels
    identifier = repository.receive(connection, message())
    repository.accept(identifier, session, lambda transaction: str(RunRepository(store.database).create_run(
        session, "request", queued=True, transaction_connection=transaction)[0]["id"]))
    repository.reject(identifier, "must not send")
    assert repository.deliveries() == []


def test_cancellation_during_send_marks_unknown_and_stops(channels):
    from types import SimpleNamespace
    from eidos_runtime.runtime.channel_service import ChannelService
    _, repository, connection, _ = channels
    identifier = repository.receive(connection, message())
    repository.reject(identifier, "answer")
    application = SimpleNamespace(repository=repository, process_pending=lambda: None,
                                  secrets=SimpleNamespace(read=lambda _: "secret"))
    async def exercise():
        started = anyio.Event()
        class Adapter:
            async def send(self, *args):
                started.set()
                await anyio.sleep_forever()
        service = ChannelService(application, lambda: True, lambda: None, lambda target: False)
        service.adapter = Adapter()
        async with anyio.create_task_group() as group:
            group.start_soon(service._work)
            with anyio.fail_after(2):
                await started.wait()
            group.cancel_scope.cancel()
    anyio.run(exercise)
    assert repository.deliveries()[0].status == "unknown"
    assert repository.claim_delivery() is None


def test_official_callback_adapter_filters_and_closes_without_a_new_loop(monkeypatch):
    import dingtalk_stream
    from eidos_runtime.infrastructure.dingtalk import _StreamClient
    received = []
    stopped = []
    async def start(client):
        handler = client.callback_handler_map[dingtalk_stream.ChatbotMessage.TOPIC]
        incoming = message().model_dump(by_alias=True)
        incoming.update(msgId=incoming.pop("msgId"), senderStaffId="user", senderCorpId="corp", senderNick="用户",
                        msgtype="text", text={"content": "测试"}, isInAtList=True)
        callback = dingtalk_stream.CallbackMessage()
        callback.data = incoming
        assert await handler.process(callback) == (dingtalk_stream.AckMessage.STATUS_OK, "OK")
        callback.data = {"msgId": "incomplete"}
        assert (await handler.process(callback))[1] == "ignored"
    async def stop(client):
        stopped.append(True)
    monkeypatch.setattr(_StreamClient, "start", start)
    monkeypatch.setattr(_StreamClient, "stop", stop)
    connection = ChannelConnection(id="connection", revision=1, config=configuration(), secret_ref="ref")
    adapter = DingTalkAdapter()
    anyio.run(adapter.listen, connection, "secret", lambda snapshot, incoming: received.append((snapshot, incoming)))
    assert received[0][1].text == "测试"
    assert received[0][1].sender_id == "user"
    assert stopped == [True]
    assert adapter.client is None


def test_callback_diagnostics_explain_ignored_fields_without_logging_secrets(monkeypatch, caplog):
    import dingtalk_stream
    from eidos_runtime.infrastructure.dingtalk import _StreamClient
    caplog.set_level(logging.INFO, logger="eidos.dingtalk")
    async def start(client):
        callback = dingtalk_stream.CallbackMessage()
        callback.data = {"msgId": "private-message-id", "senderCorpId": "corp", "robotCode": "robot",
                         "conversationId": "group", "conversationType": "2", "senderStaffId": "user",
                         "senderNick": "private-name", "isInAtList": True, "msgtype": "text",
                         "text": {"content": "private-message-text"}, "sessionWebhook": "https://private-webhook"}
        handler = client.callback_handler_map[dingtalk_stream.ChatbotMessage.TOPIC]
        assert (await handler.process(callback))[1] == "OK"
        callback.data["senderCorpId"] = None
        assert (await handler.process(callback))[1] == "ignored"
        client.logger.info("connecting to endpoint %s", "https://private-endpoint?ticket=private-ticket")
        client.logger.warning("invalid message, content=%r", callback.data)
        try:
            raise ValueError("private-exception-secret")
        except ValueError:
            client.logger.exception("error processing message")
        client.logger.error("private-formatted-response-secret")
    monkeypatch.setattr(_StreamClient, "start", start)
    connection = ChannelConnection(id="connection", revision=1, config=configuration(), secret_ref="ref")
    anyio.run(DingTalkAdapter().listen, connection, "private-client-secret", lambda *_: None)
    assert "callback parsed" in caplog.text
    assert "reason=invalid_fields fields=corp_id" in caplog.text
    assert "event=connecting" in caplog.text
    assert "event=invalid_frame" in caplog.text
    assert "event=callback_failed exception=ValueError" in caplog.text
    assert "private-" not in caplog.text


def test_stream_frame_diagnostics_observe_sdk_duplicate_ack_without_exposing_payload(caplog):
    import dingtalk_stream
    from eidos_runtime.infrastructure.dingtalk import _StreamClient
    caplog.set_level(logging.INFO, logger="eidos.dingtalk")
    handled = []
    acks = []
    class Handler(dingtalk_stream.CallbackHandler):
        async def process(self, callback):
            handled.append(callback.data)
            return 200, "OK"
    class Websocket:
        async def send(self, data):
            acks.append(data)
    client = _StreamClient(dingtalk_stream.Credential("app", "private-secret"))
    client.register_callback_handler(dingtalk_stream.ChatbotMessage.TOPIC, Handler())
    frame = {"type": "CALLBACK", "headers": {"messageId": "private-frame-id", "topic": dingtalk_stream.ChatbotMessage.TOPIC},
             "data": json.dumps({"text": "private-text", "sessionWebhook": "private-webhook"})}
    async def exercise():
        await client.background_task(frame, Websocket())
        await client.background_task(frame, Websocket())
    anyio.run(exercise)
    assert len(handled) == 1
    assert len(acks) == 2
    assert caplog.text.count("Stream callback frame") == 2
    assert "private-" not in caplog.text


def test_message_size_limits_are_utf8_bounded(channels):
    _, repository, connection, _ = channels
    identifier = repository.receive(connection, message())
    repository.reject(identifier, "文" * 20_000)
    deliveries = repository.deliveries()
    assert len(deliveries) <= 5
    assert all(len(value.text.encode()) <= 6000 for value in deliveries)
    assert any("完整回答" in value.text for value in deliveries)


def test_secrets_are_owner_only_bounded_and_reject_symlinks(tmp_path):
    store = ChannelSecrets(tmp_path)
    reference = store.write("private-value")
    assert store.read(reference) == "private-value"
    assert (store.directory / reference).stat().st_mode & 0o777 == 0o600
    (store.directory / reference).chmod(0o644)
    with pytest.raises(ChannelError):
        store.read(reference)
    (store.directory / reference).unlink()
    target = tmp_path / "external"
    target.write_text("secret")
    (store.directory / reference).symlink_to(target)
    with pytest.raises(ChannelError):
        store.read(reference)
    with pytest.raises(ChannelError):
        store.read("../external")


def test_migration_failure_rolls_back_partial_tables_and_version():
    connection = sqlite3.connect(":memory:")
    connection.executescript("CREATE TABLE channel_sessions(id TEXT); PRAGMA user_version=29;")
    with pytest.raises(sqlite3.OperationalError):
        migrate_channels(connection)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 29
    assert connection.execute("SELECT name FROM sqlite_master WHERE name='channel_connections'").fetchone() is None
    assert not connection.in_transaction


def test_existing_v29_database_upgrades_without_losing_sessions(tmp_path):
    directory = tmp_path / "data"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(directory)
    store.initialize()
    session_id = store.create_session(str(workspace))["id"]
    store.connection.executescript("DROP TRIGGER channel_memory_insert; DROP TRIGGER channel_origin_memory; DROP TABLE channel_deliveries; DROP TABLE channel_run_origins; DROP TABLE channel_inbound; DROP TABLE channel_sessions; DROP TABLE channel_connections; PRAGMA user_version=29;")
    store.close()
    restored = SessionStore(directory)
    try:
        restored.initialize()
        assert restored.read_session(session_id)["id"] == session_id
        assert restored.connection.execute("PRAGMA user_version").fetchone()[0] == 30
        assert ChannelRepository(restored.database).connection() is None
        assert restored.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        restored.close()


@pytest.mark.parametrize("direct", [False, True])
def test_openapi_token_send_target_and_receipt(monkeypatch, direct):
    calls = []
    def handle(request):
        calls.append((request.url.path, json.loads(request.content)))
        if request.url.path.endswith("accessToken"):
            return httpx.Response(200, json={"accessToken": "token", "expireIn": 7200})
        assert request.headers["x-acs-dingtalk-access-token"] == "token"
        return httpx.Response(200, json={"processQueryKey": "receipt"})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    adapter = DingTalkAdapter()
    connection = ChannelConnection(id="connection", revision=1, config=configuration(), secret_ref="ref")
    anyio.run(adapter.send, connection, "secret", message(conversation_type="1" if direct else "2"), "answer")
    assert calls[0] == ("/v1.0/oauth2/accessToken", {"appKey": "app", "appSecret": "secret"})
    assert calls[1][1]["robotCode"] == "robot"
    assert calls[1][1]["userIds" if direct else "openConversationId"] == (["user"] if direct else "group")
    assert json.loads(calls[1][1]["msgParam"])["text"] == "answer"


@pytest.mark.parametrize("status,uncertain,retry", [(429, False, True), (500, True, False), (403, False, False)])
def test_send_classifies_known_failure_and_uncertain_result(monkeypatch, status, uncertain, retry):
    def handle(request):
        return httpx.Response(200, json={"accessToken": "token", "expireIn": 7200}) if request.url.path.endswith("accessToken") else httpx.Response(status)
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    connection = ChannelConnection(id="connection", revision=1, config=configuration(), secret_ref="ref")
    with pytest.raises(DingTalkSendError) as raised:
        anyio.run(DingTalkAdapter().send, connection, "secret", message(), "answer")
    assert raised.value.uncertain == uncertain
    assert raised.value.retry == retry


@pytest.mark.parametrize("approval_mode", ["manual", "full_access"])
def test_real_runtime_receives_once_runs_and_persists_final_reply(tmp_path, approval_mode):
    from eidos_runtime.application.errors import ApplicationError
    from eidos_runtime.protocol.methods import RunStartRequestDto
    output = io.StringIO()
    target = tmp_path / "outside-workspace.txt"
    responses = [ModelResponse(text="渠道任务已完成。")]
    if approval_mode == "full_access":
        responses.insert(0, ModelResponse(tool_calls=(ModelToolCall("shell", "run_shell", {
            "command": f"printf 'channel-full-access' > {shlex.quote(str(target))}"}),)))
    model = ScriptedModel(responses)
    server = RuntimeServer(output, tmp_path / "data", model=model)
    try:
        server.handle({"jsonrpc": "2.0", "id": "client-init", "method": "initialize", "params": {"protocolVersion": 1, "client": {"name": "test", "version": "1"}}})
        assert server.initialized, output.getvalue()
        service = server.channel_service
        service.active = lambda: False
        configured = server.model_config.create(provider_id="deepseek", model_id=DEFAULT_MODEL_ID, api_key="test-key")
        application = service.application
        application.save(ChannelSaveRequest(config=configuration(approval_mode=approval_mode).model_copy(update={"model_id": configured.id, "reasoning_selection": "high"}),
            expected_revision=0, client_secret="test-channel-secret",
            full_access_confirmation="full-access-v1" if approval_mode == "full_access" else None))
        connection = application.repository.connection()
        connection = connection.model_copy(update={"revision": 2, "config": connection.config.model_copy(update={"enabled": True})})
        application.repository.save(connection, 1)
        application.receive(connection, message())
        application.receive(connection, message())
        application.process_pending()
        identifier = application.repository.inbound(application.repository._connection().execute("SELECT id FROM channel_inbound").fetchone()[0])
        assert identifier.run_id
        assert server.store.read_model_profile(identifier.run_id).reasoning_selection == "high"
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and server.store.read_run(identifier.run_id)["status"] in {"queued", "running", "finalizing"}:
            time.sleep(0.02)
        assert server.store.read_run(identifier.run_id)["status"] == "succeeded", output.getvalue()
        assert server.store.read_run(identifier.run_id).get("approvalMode", "manual") == approval_mode
        source = [item["content"] for context in model.contexts for item in context if item.get("sectionId") == "channel-origin"]
        assert source
        assert all(("do not request per-operation approvals" in text) == (approval_mode == "full_access") for text in source)
        if approval_mode == "full_access":
            assert target.read_text() == "channel-full-access"
            assert server.store.connection.execute("SELECT sandbox_type FROM tool_attempts").fetchone()[0] == "none"
            assert server.store.connection.execute("SELECT count(*) FROM approvals").fetchone()[0] == 0
            assert server.store.connection.execute("SELECT count(*) FROM durable_intents").fetchone()[0] == 1
        forged_mode = "full_access" if approval_mode == "manual" else "manual"
        with pytest.raises(ApplicationError, match="已确认的渠道配置"):
            application.runs.start(RunStartRequestDto(sessionId=identifier.session_id, userInput="switch mode", modelId=configured.id,
                approvalMode=forged_mode, fullAccessConfirmation="full-access-v1" if forged_mode == "full_access" else None),
                channel_message_id=identifier.id)
        application.repository.collect_replies()
        assert application.repository.deliveries()[0].text == "渠道任务已完成。"
        assert server.store.connection.execute("SELECT count(*) FROM runs WHERE id=?", (identifier.run_id,)).fetchone()[0] == 1
        assert server.store.approval_user_evidence(identifier.run_id) == ()
        response = application.read().model_dump_json()
        assert "test-channel-secret" not in response
        assert "secret_ref" not in response
        snapshot = server.store.read_session_snapshot(identifier.session_id)
        assert snapshot["runs"][0]["channelOrigin"]["senderId"] == "user"
    finally:
        server.close()


def test_channel_fixture_matches_python_contracts():
    from eidos_runtime.protocol.channels import ChannelResponse
    fixture = json.loads((Path(__file__).parents[2] / "protocol/fixtures/channels-v1.json").read_text())
    for value in fixture["validRequests"]:
        ChannelSaveRequest.model_validate(value)
    for value in fixture["invalidRequests"]:
        with pytest.raises(ValidationError):
            ChannelSaveRequest.model_validate(value)
    for value in fixture["validResponses"]:
        ChannelResponse.model_validate(value)


@pytest.mark.parametrize("direct", [False, True])
def test_managed_stream_callback_to_run_and_openapi_delivery(tmp_path, monkeypatch, direct):
    import dingtalk_stream
    from eidos_runtime.infrastructure.dingtalk import _StreamClient
    sent = []
    async def start(client):
        callback = dingtalk_stream.CallbackMessage()
        callback.data = {"msgId": "from-stream", "senderCorpId": "corp", "robotCode": "robot",
                         "conversationId": "group", "conversationType": "1" if direct else "2", "senderStaffId": "user",
                         "senderNick": "用户", "isInAtList": not direct, "msgtype": "text", "text": {"content": "检查状态。"}}
        await client.callback_handler_map[dingtalk_stream.ChatbotMessage.TOPIC].process(callback)
        await anyio.sleep_forever()
    async def send(adapter, connection, secret, incoming, text):
        sent.append((incoming.msg_id, text))
    monkeypatch.setattr(_StreamClient, "start", start)
    monkeypatch.setattr(DingTalkAdapter, "send", send)
    output = io.StringIO()
    server = RuntimeServer(output, tmp_path / "data", model=ScriptedModel([ModelResponse(text="自动渠道回复。")]))
    try:
        server.handle({"jsonrpc": "2.0", "id": "client-init", "method": "initialize", "params": {"protocolVersion": 1, "client": {"name": "test", "version": "1"}}})
        assert server.initialized
        configured = server.model_config.create(provider_id="deepseek", model_id=DEFAULT_MODEL_ID, api_key="test-key")
        service = server.channel_service
        service.application.save(ChannelSaveRequest(config=configuration(enabled=True).model_copy(update={"model_id": configured.id}),
            expected_revision=0, client_secret="channel-secret"))
        deadline = time.monotonic() + 10
        while not sent and time.monotonic() < deadline:
            time.sleep(0.02)
        assert sent == [("from-stream", "自动渠道回复。")], output.getvalue()
        while service.application.repository.deliveries()[0].status != "sent" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert service.application.repository.deliveries()[0].status == "sent"
        assert service._preparing.is_set() is False
    finally:
        server.close()
    assert server.supervisor.resources.active_resources() == ()


@pytest.mark.parametrize("selection", [None, "none", "thinking", "low", "medium", "high", "max"])
def test_channel_reasoning_persists_across_restart(channels, selection):
    store, repository, connection, _ = channels
    saved = connection.model_copy(update={"revision": 2, "config": configuration(reasoning_selection=selection)})
    repository.save(saved, 1)
    data_directory = store.data_directory
    store.close()
    reopened = SessionStore(data_directory)
    try:
        reopened.initialize()
        recovered = ChannelRepository(reopened.database).connection()
        assert recovered.config.reasoning_selection == selection
    finally:
        reopened.close()


def test_channel_reasoning_rejects_unknown_values():
    with pytest.raises(ValidationError):
        ChannelConfig.model_validate(configuration().model_dump() | {"reasoningSelection": "ultra"})

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

import anyio
import httpx2 as httpx
import pytest
from pydantic import ValidationError

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.application.web_search import WebSearchApplication
from eidos_runtime.db.schema import V33_SCHEMA_SQL
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.db.web_search_migration import migrate_web_search
from eidos_runtime.domain.web_search import (
    RunWebSearchSnapshot,
    WebSearchConfig,
    LangSearchOptions,
)
from eidos_runtime.infrastructure.secret_files import SecretFiles
from eidos_runtime.extensions.plugins import PluginCatalog
from eidos_runtime.extensions.skills import SkillCatalog, deploy_system_skills
from eidos_runtime.infrastructure.web_search import (
    search,
    SearchFailure,
    request_payload,
)
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.persistence.web_search import WebSearchRepository
from eidos_runtime.protocol.methods import RunStartRequestDto, SessionCreateRequestDto
from eidos_runtime.protocol.server import RuntimeServer
from eidos_runtime.protocol.web_search import WebSearchSaveRequest
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel
from eidos_runtime.runtime.run_resources import RunResources
from eidos_runtime.tools.web_search import DeepSeekSearchInput, WebSearchInput


@pytest.fixture
def configured(tmp_path):
    store = SessionStore(tmp_path / "data")
    store.initialize()
    assert store.health_state == "ready"
    repository = WebSearchRepository(store.database)
    secrets = SecretFiles(store.data_directory / "web-search-secrets")
    application = WebSearchApplication(repository, secrets)
    yield store, repository, secrets, application
    store.close()


def save(
    application,
    provider="langsearch",
    *,
    revision=None,
    key="fixture-search-key",
    config=None,
):
    return application.save(
        WebSearchSaveRequest(
            expected_revision=application.read().revision
            if revision is None
            else revision,
            config=config or WebSearchConfig(default_provider=provider),
            credential_changes=[{"provider": provider, "action": "set", "value": key}]
            if key
            else [],
        )
    )


def snapshot(provider="langsearch", **options):
    return RunWebSearchSnapshot(
        revision=1,
        provider=provider,
        credential_ref=str(uuid4()),
        config=WebSearchConfig.model_validate(
            {"defaultProvider": provider, provider: options}
        ),
    )


def session(store, tmp_path):
    path = tmp_path / str(uuid4())
    path.mkdir()
    return store.create_session(str(path))["id"]


def test_default_is_explicit_and_keys_never_enter_sqlite_or_public_response(
    configured, tmp_path
):
    store, repo, secrets, app = configured
    assert app.read().config.default_provider is None
    result = save(app, config=WebSearchConfig())
    assert result.config.default_provider is None
    assert result.credentials.langsearch == "stored"
    assert not hasattr(result, "credential_values")
    assert "fixture-search-key" not in result.model_dump_json()
    assert "credential_ref" not in result.model_dump_json()
    assert "fixture-search-key" not in "\n".join(store.connection.iterdump())
    reference = repo.read().credentials.langsearch
    assert os.stat(secrets.directory).st_mode & 0o777 == 0o700
    assert os.stat(secrets.directory / reference).st_mode & 0o777 == 0o600
    run, _ = store.create_run(session(store, tmp_path), "no default")
    assert repo.snapshot(run["id"]) is None
    with pytest.raises(ApplicationError, match="API Key"):
        app.save(
            WebSearchSaveRequest(
                expected_revision=1, config=WebSearchConfig(default_provider="deepseek")
            )
        )


def test_cas_rotation_clear_and_child_inherit_freeze_in_one_transaction(
    configured, tmp_path
):
    store, repo, secrets, app = configured
    save(app)
    run, _ = store.create_run(session(store, tmp_path), "first")
    original = repo.snapshot(run["id"])
    queued, _ = store.enqueue_run(run["sessionId"], "queued")
    with pytest.raises(ApplicationError) as conflict:
        save(app, "deepseek", revision=0)
    assert conflict.value.code == "WEB_SEARCH_CONFIG_CONFLICT"
    save(app, key="rotated-search-key")
    assert secrets.read(original.credential_ref) == "fixture-search-key"
    assert repo.snapshot(queued["id"]) == original
    new, _ = store.create_run(session(store, tmp_path), "new")
    assert repo.snapshot(new["id"]).credential_ref != original.credential_ref
    with store.database.transaction() as transaction:
        WebSearchRepository.inherit(transaction, new["id"], run["id"])
    assert repo.snapshot(new["id"]) == original
    app.save(
        WebSearchSaveRequest(
            expected_revision=2,
            config=app.read().config,
            credential_changes=[{"provider": "langsearch", "action": "clear"}],
        )
    )
    assert app.read().config.default_provider is None
    assert secrets.read(original.credential_ref) == "fixture-search-key"
    other, _ = store.create_run(session(store, tmp_path), "disabled")
    assert repo.snapshot(other["id"]) is None
    # A failed Run creation must roll its snapshot back too.
    with pytest.raises(RuntimeError):
        with store.database.transaction() as transaction:
            transaction.execute(
                "DELETE FROM run_web_search_snapshots WHERE run_id=?", (run["id"],)
            )
            raise RuntimeError("rollback")
    assert repo.snapshot(run["id"]) == original


def test_restart_and_secret_permissions(configured, tmp_path):
    store, repo, secrets, app = configured
    save(app)
    reference = repo.read().credentials.langsearch
    os.chmod(secrets.directory / reference, 0o644)
    assert app.read().credentials.langsearch == "unavailable"
    with pytest.raises(ApplicationError):
        save(app, key=None)
    os.chmod(secrets.directory / reference, 0o600)
    directory = store.data_directory
    store.close()
    reopened = SessionStore(directory)
    reopened.initialize()
    try:
        restored = WebSearchApplication(
            WebSearchRepository(reopened.database), secrets
        ).read()
        assert restored.config.default_provider == "langsearch"
        assert restored.credentials.langsearch == "stored"
    finally:
        reopened.close()


def test_upgrade_keeps_existing_runs_disabled_and_migration_rolls_back(tmp_path):
    connection = sqlite3.connect(tmp_path / "old.sqlite")
    connection.executescript(V33_SCHEMA_SQL + "PRAGMA user_version=33;")
    migrate_web_search(connection)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 34
    assert (
        connection.execute("SELECT count(*) FROM run_web_search_snapshots").fetchone()[
            0
        ]
        == 0
    )
    connection.close()
    broken = sqlite3.connect(":memory:")
    broken.execute("CREATE TABLE run_web_search_snapshots(id TEXT)")
    broken.execute("PRAGMA user_version=33")
    with pytest.raises(sqlite3.Error):
        migrate_web_search(broken)
    assert broken.execute("PRAGMA user_version").fetchone()[0] == 33
    assert not broken.execute(
        "SELECT 1 FROM sqlite_master WHERE name='web_search_settings'"
    ).fetchone()
    broken.close()


@pytest.mark.parametrize(
    "changes",
    [
        {"freshness": "yesterday"},
        {"freshness": "2026-02-31"},
        {"freshness": "2026-10-20..2026-10-19"},
        {"includeDomains": ["https://example.com"]},
        {"includeDomains": ["example.com"], "excludeDomains": ["EXAMPLE.COM"]},
    ],
)
def test_invalid_settings(changes):
    with pytest.raises(ValidationError):
        LangSearchOptions.model_validate(changes)


def test_query_contract_and_payload_match_provider():
    with pytest.raises(ValidationError):
        DeepSeekSearchInput(query="a" * 129)
    with pytest.raises(ValidationError):
        WebSearchInput(query="   ")
    assert WebSearchInput(query="a" * 129).query
    assert "contents" not in request_payload(snapshot(), "query", 5)
    body = request_payload(snapshot(includePageContent=True), "query", 5)
    assert body["contents"] == {"text": {"maxCharacters": 3000}}
    body = request_payload(snapshot("deepseek", localization="203.0.113.1"), "query", 5)
    assert body == {
        "query": "query",
        "count": 5,
        "need_content": False,
        "localization": "203.0.113.1",
    }


@pytest.mark.parametrize("provider", ["deepseek", "langsearch"])
def test_official_request_parse_order_nullable_dates_text_and_bounds(provider):
    requests = []

    def handle(request):
        requests.append(request)
        hits = [
            {
                "title": "title",
                "name": "name",
                "url": "https://example.com/first",
                "summary": "summary",
                "text": "正文" * 10000,
                "content": "正文" * 10000,
            },
            {"url": "javascript:alert(1)"},
            {"url": "https://example.com/last"},
        ]
        body = (
            {"results": hits}
            if provider == "deepseek"
            else {
                "code": 200,
                "data": {"webPages": {"value": hits}},
                "usage": {"input_tokens": 2, "output_tokens": 10},
            }
        )
        return httpx.Response(200, json=body)

    async def run():
        return await search(
            snapshot(provider, includePageContent=True, maxExcerptCharacters=100),
            "key",
            "query",
            5,
            transport=httpx.MockTransport(handle),
        )

    result = anyio.run(run)
    assert [r.url for r in result.results] == [
        "https://example.com/first",
        "https://example.com/last",
    ]
    assert result.results[0].published_at is None
    assert len(result.results[0].excerpt) == 100
    assert result.truncated
    assert result.results[0].content_kind == "text"
    assert len(requests) == 1
    assert requests[0].headers["authorization"] == "Bearer key"
    assert str(requests[0].url) == (
        "https://api.deepseek.com/v1/tools/search"
        if provider == "deepseek"
        else "https://api.langsearch.com/v1/web-search"
    )


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "web_search_auth_failed"),
        (403, "web_search_access_denied"),
        (429, "web_search_rate_limited"),
        (503, "web_search_unavailable"),
        (302, "web_search_request_failed"),
    ],
)
def test_http_failure_never_retries_or_follows_redirects(status, code):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            status,
            headers={"location": "https://other.invalid/", "retry-after": "1"},
            text="Authorization: fixture-secret",
        )

    async def run():
        return await search(
            snapshot(), "key", "query", 5, transport=httpx.MockTransport(handle)
        )

    with pytest.raises(SearchFailure) as raised:
        anyio.run(run)
    assert raised.value.code == code and len(calls) == 1
    assert "fixture-secret" not in raised.value.message


@pytest.mark.parametrize(
    "body,code",
    [
        ({"code": "429"}, "web_search_rate_limited"),
        ({"code": 200}, "web_search_invalid_response"),
        (
            {
                "code": 200,
                "data": {"webPages": {"value": [{"url": "file:///etc/passwd"}]}},
            },
            "web_search_invalid_response",
        ),
    ],
)
def test_invalid_and_business_failure(body, code):
    async def run():
        return await search(
            snapshot(),
            "key",
            "query",
            5,
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body)),
        )

    with pytest.raises(SearchFailure) as raised:
        anyio.run(run)
    assert raised.value.code == code


def test_empty_response_and_oversize():
    async def run(body):
        return await search(
            snapshot("deepseek"),
            "key",
            "query",
            5,
            transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body)),
        )

    assert anyio.run(run, b'{"results":[]}').results == []
    with pytest.raises(SearchFailure) as raised:
        anyio.run(run, b" " * (2 * 1024 * 1024 + 1))
    assert raised.value.code == "web_search_response_too_large"


def test_registry_exposure_requires_explicit_default_and_readable_key(
    configured, tmp_path
):
    store, repo, secrets, app = configured
    deploy_system_skills(store.data_directory)
    extensions = SkillCatalog(PluginCatalog(store)).extension_snapshot()
    kernel = RuntimeAsyncKernel()
    kernel.start()
    try:
        for default in (None, "langsearch"):
            save(app, config=WebSearchConfig(default_provider=default))
            run, _ = store.create_run(session(store, tmp_path), "tool catalog")
            with RunResources(
                store, run["id"], extensions, async_kernel=kernel
            ) as resources:
                names = [entry.spec.name for entry in resources.registry.entries]
                assert ("web_search" in names) == bool(default)
        snap = repo.snapshot(run["id"])
        os.chmod(secrets.directory / snap.credential_ref, 0o644)
        with RunResources(
            store, run["id"], extensions, async_kernel=kernel
        ) as resources:
            assert "web_search" not in [
                entry.spec.name for entry in resources.registry.entries
            ]
    finally:
        kernel.close()


def start_server(tmp_path, model):
    output = io.StringIO()
    server = RuntimeServer(output, tmp_path / "data", model=model)
    server.handle(
        {
            "jsonrpc": "2.0",
            "id": "client-init",
            "method": "initialize",
            "params": {
                "protocolVersion": 1,
                "client": {"name": "test", "version": "1"},
            },
        }
    )
    assert server.initialized, output.getvalue()
    return server, output


def wait_for(predicate, output):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    pytest.fail(output.getvalue())


@pytest.mark.parametrize(
    "mode,decision",
    [("full_access", "approve"), ("manual", "approve"), ("manual", "reject")],
)
def test_real_runtime_search_approval_intent_result_and_no_shell_network_grant(
    tmp_path, monkeypatch, mode, decision
):
    from eidos_runtime.infrastructure.web_search import SearchPage, SearchHit

    calls = []

    async def fake(snapshot, key, query, count):
        calls.append((snapshot.provider, key, query, count))
        return SearchPage(
            results=[
                SearchHit(
                    title="source",
                    url="https://example.com",
                    excerpt="evidence",
                    content_kind="snippet",
                )
            ],
            truncated=False,
            retrieved_at="2026-10-21T00:00:00+00:00",
        )

    monkeypatch.setattr("eidos_runtime.tools.web_search.search", fake)
    model = ScriptedModel(
        [
            ModelResponse(
                tool_calls=(
                    ModelToolCall("search-call", "web_search", {"query": "query"}),
                )
            ),
            ModelResponse(text="已完成。"),
        ]
    )
    server, output = start_server(tmp_path, model)
    try:
        save(server._web_search_application())
        configured_model = server.model_config.create(
            provider_id="deepseek",
            model_id="deepseek-v4-flash",
            api_key="fixture-model-key",
        )
        sessions = server._applications_or_error().sessions
        created = sessions.create(SessionCreateRequestDto(operationId=str(uuid4())))
        outcome = server._applications_or_error().runs.start(
            RunStartRequestDto(
                sessionId=created.id,
                userInput="搜索 query",
                modelId=configured_model.id,
                approvalMode=mode,
                fullAccessConfirmation="full-access-v1"
                if mode == "full_access"
                else None,
            )
        )
        outcome.release_after_commit()
        run_id = outcome.response.id
        if mode == "manual":
            wait_for(
                lambda: server.store.typed_runtime_repository().read_pending_approval(
                    run_id
                ),
                output,
            )
            assert not calls
            request_id = wait_for(
                lambda: next(
                    (
                        json.loads(line)["id"]
                        for line in output.getvalue().splitlines()
                        if json.loads(line).get("method") == "item/requestApproval"
                    ),
                    None,
                ),
                output,
            )
            if decision == "approve":
                server._applications_or_error().approvals.approve(request_id)
            else:
                server._applications_or_error().approvals.reject(request_id)
        wait_for(
            lambda: server.store.read_run(run_id)["status"]
            in {"succeeded", "failed", "interrupted"},
            output,
        )
        assert server.store.read_run(run_id)["status"] == "succeeded", output.getvalue()
        assert calls == (
            [("langsearch", "fixture-search-key", "query", 5)]
            if decision == "approve"
            else []
        )
        assert server.store.connection.execute(
            "SELECT count(*) FROM durable_intents"
        ).fetchone()[0] == (1 if decision == "approve" else 0)
        assert server.store.connection.execute(
            "SELECT count(*) FROM approvals"
        ).fetchone()[0] == (1 if mode == "manual" else 0)
        assert server.store.run_permission_grants(run_id).network is None
        rows = server.store.connection.execute(
            "SELECT result_json FROM tool_calls WHERE tool_name='web_search'"
        ).fetchall()
        assert len(rows) == 1 and json.loads(rows[0][0])["outcome"] == (
            "success" if decision == "approve" else "error"
        )
        assert "fixture-search-key" not in output.getvalue()
        assert "fixture-search-key" not in json.dumps(model.contexts, default=str)
    finally:
        server.close()


def test_shared_protocol_fixture_matches_python_contract():
    from eidos_runtime.protocol.web_search import (
        WebSearchReadRequest,
        WebSearchResponse,
    )

    fixture = json.loads(
        (Path(__file__).parents[2] / "protocol/fixtures/web-search-v1.json").read_text()
    )
    for name, model in [
        ("ReadRequests", WebSearchReadRequest),
        ("Requests", WebSearchSaveRequest),
        ("Responses", WebSearchResponse),
    ]:
        for value in fixture["valid" + name]:
            model.model_validate(value)
        for value in fixture["invalid" + name]:
            with pytest.raises(ValidationError):
                model.model_validate(value)


def test_timeouts_and_connect_errors_are_reported_without_retry():
    for exception, code in [
        (httpx.ReadTimeout("timeout"), "web_search_timeout"),
        (httpx.ConnectError("connect"), "web_search_network_error"),
    ]:
        calls = []

        def handle(request):
            calls.append(request)
            raise exception

        async def run():
            return await search(
                snapshot(), "key", "query", 5, transport=httpx.MockTransport(handle)
            )

        with pytest.raises(SearchFailure) as raised:
            anyio.run(run)
        assert raised.value.code == code and len(calls) == 1


def test_secret_cleanup_retains_queued_runs_and_removes_unreferenced_versions(
    configured, tmp_path
):
    store, repo, secrets, app = configured
    save(app)
    run, _ = store.create_run(session(store, tmp_path), "retain")
    original = repo.snapshot(run["id"])
    orphan = secrets.write("orphan")
    save(app, key="replacement")
    assert not (secrets.directory / orphan).exists()
    assert secrets.read(original.credential_ref) == "fixture-search-key"
    with store.database.transaction() as transaction:
        transaction.execute(
            "UPDATE runs SET status='canceled' WHERE id=?", (run["id"],)
        )
    save(app, key=None)
    assert not (secrets.directory / original.credential_ref).exists()


def test_failed_search_returns_to_model_without_provider_switch(tmp_path, monkeypatch):
    calls = []

    async def fake(snapshot, key, query, count):
        calls.append(snapshot.provider)
        raise SearchFailure("web_search_rate_limited", "限流", sent=True)

    monkeypatch.setattr("eidos_runtime.tools.web_search.search", fake)
    model = ScriptedModel(
        [
            ModelResponse(
                tool_calls=(
                    ModelToolCall("search-call", "web_search", {"query": "query"}),
                )
            ),
            ModelResponse(text="服务限流，我会继续处理其他内容。"),
        ]
    )
    server, output = start_server(tmp_path, model)
    try:
        app = server._web_search_application()
        save(app)
        save(app, "deepseek")
        # Both providers have credentials, but LangSearch remains the explicit default.
        app.save(
            WebSearchSaveRequest(
                expected_revision=2,
                config=WebSearchConfig(default_provider="langsearch"),
            )
        )
        profile = server.model_config.create(
            provider_id="deepseek",
            model_id="deepseek-v4-flash",
            api_key="fixture-model-key",
        )
        created = server._applications_or_error().sessions.create(
            SessionCreateRequestDto(operationId=str(uuid4()))
        )
        outcome = server._applications_or_error().runs.start(
            RunStartRequestDto(
                sessionId=created.id,
                userInput="搜索",
                modelId=profile.id,
                approvalMode="full_access",
                fullAccessConfirmation="full-access-v1",
            )
        )
        outcome.release_after_commit()
        run_id = outcome.response.id
        wait_for(
            lambda: server.store.read_run(run_id)["status"] in {"succeeded", "failed"},
            output,
        )
        assert server.store.read_run(run_id)["status"] == "succeeded", output.getvalue()
        assert calls == ["langsearch"]
        result = json.loads(
            server.store.connection.execute(
                "SELECT result_json FROM tool_calls WHERE tool_name='web_search'"
            ).fetchone()[0]
        )
        assert (
            result["code"] == "web_search_rate_limited"
            and not result["reconciliationRequired"]
        )
        assert len(model.contexts) >= 2
    finally:
        server.close()


def test_cancel_cleans_owned_network_task_and_late_result_cannot_replace_cancellation(
    tmp_path, monkeypatch
):
    from eidos_runtime.infrastructure.web_search import SearchPage
    import threading

    started, cleaned = threading.Event(), threading.Event()

    async def fake(snapshot, key, query, count):
        started.set()
        try:
            await anyio.sleep_forever()
        finally:
            cleaned.set()
        return SearchPage(
            results=[], truncated=False, retrieved_at="2026-10-21T00:00:00+00:00"
        )

    monkeypatch.setattr("eidos_runtime.tools.web_search.search", fake)
    model = ScriptedModel(
        [
            ModelResponse(
                tool_calls=(
                    ModelToolCall("search-call", "web_search", {"query": "query"}),
                )
            )
        ]
    )
    server, output = start_server(tmp_path, model)
    try:
        save(server._web_search_application())
        profile = server.model_config.create(
            provider_id="deepseek",
            model_id="deepseek-v4-flash",
            api_key="fixture-model-key",
        )
        created = server._applications_or_error().sessions.create(
            SessionCreateRequestDto(operationId=str(uuid4()))
        )
        outcome = server._applications_or_error().runs.start(
            RunStartRequestDto(
                sessionId=created.id,
                userInput="搜索",
                modelId=profile.id,
                approvalMode="full_access",
                fullAccessConfirmation="full-access-v1",
            )
        )
        outcome.release_after_commit()
        assert started.wait(5), output.getvalue()
        server.supervisor.cancel_run(outcome.response.id)
        assert cleaned.wait(5)
        wait_for(
            lambda: server.store.read_run(outcome.response.id)["status"] == "canceled",
            output,
        )
        result = json.loads(
            server.store.connection.execute(
                "SELECT result_json FROM tool_calls WHERE tool_name='web_search'"
            ).fetchone()[0]
        )
        assert result["outcome"] != "success"
        assert not [
            d
            for d in server.async_kernel.recent_task_diagnostics()
            if d.owner_id == outcome.response.id and d.state == "running"
        ]
    finally:
        server.close()


def test_run_operation_replay_keeps_original_search_snapshot(configured, tmp_path):
    store, repo, _, app = configured
    save(app)
    session_id = session(store, tmp_path)
    operation = str(uuid4())
    first, _ = store.create_run(session_id, "same operation", operation_id=operation)
    frozen = repo.snapshot(first["id"])
    save(app, "deepseek")
    replay, _ = store.create_run(session_id, "same operation", operation_id=operation)
    assert replay["id"] == first["id"]
    assert repo.snapshot(first["id"]) == frozen
    assert (
        store.connection.execute(
            "SELECT count(*) FROM run_web_search_snapshots"
        ).fetchone()[0]
        == 1
    )


@pytest.mark.parametrize("enabled", [False, True])
def test_actual_child_run_inherits_parent_search_including_disabled_state(
    configured, tmp_path, enabled
):
    from eidos_runtime.domain.collaboration import SpawnAgent
    from eidos_runtime.persistence.collaboration import CollaborationRepository

    store, repo, _, app = configured
    if enabled:
        save(app)
    parent, _ = store.create_run(session(store, tmp_path), "delegate")
    frozen = repo.snapshot(parent["id"])
    save(app, "deepseek")
    item = store.create_tool_item(parent["id"], 1, 0, "spawn-call", "spawn_agent", "{}")
    child = CollaborationRepository(store.database).spawn(
        parent["id"],
        item["id"],
        SpawnAgent(
            task_name="search-task", message="Read and report.", role="explorer"
        ),
    )
    assert repo.snapshot(child.run_id) == frozen


def test_large_source_metadata_and_escaped_content_fit_canonical_budget(monkeypatch):
    from eidos_runtime.infrastructure import web_search as driver
    from eidos_runtime.tools.web_search import WebSearchAdapter

    hits = [
        {
            "url": "https://example.com/" + "a" * 8000 + str(index),
            "name": "标题" * 400,
            "text": '\x00"\\' * 1000,
        }
        for index in range(50)
    ]

    async def fetch(snap, key, query, count):
        return await driver.search(
            snap,
            key,
            query,
            count,
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200, json={"code": 200, "data": {"webPages": {"value": hits}}}
                )
            ),
        )

    monkeypatch.setattr("eidos_runtime.tools.web_search.search", fetch)

    class Credentials:
        def read(self, reference):
            return "fixture-credential"

    adapter = WebSearchAdapter(
        snapshot(includePageContent=True, maxExcerptCharacters=10000), Credentials()
    )
    result = anyio.run(adapter.search, {"query": "\x00" * 2047 + "q", "count": 50})
    assert result["outcome"] == "success"
    assert result["data"]["truncated"]
    assert len(json.dumps(result, ensure_ascii=False).encode()) <= 256 * 1024
    assert len(result["data"]["results"]) < 50

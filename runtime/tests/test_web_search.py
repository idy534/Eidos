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
    DoubaoSearchOptions,
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
from eidos_runtime.tools.web_search import (
    DeepSeekSearchInput,
    DoubaoSearchInput,
    WebSearchInput,
    web_search_entry,
)


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


def test_default_is_explicit_and_local_key_display_never_enters_sqlite(
    configured, tmp_path
):
    store, repo, secrets, app = configured
    assert app.read().config.default_provider is None
    result = save(app, config=WebSearchConfig())
    assert result.config.default_provider is None
    assert result.credentials.langsearch == "stored"
    assert result.credential_values.langsearch == "fixture-search-key"
    assert result.to_wire_dict()["credentialValues"]["langsearch"] == "fixture-search-key"
    assert "fixture-search-key" not in repr(result)
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


@pytest.mark.parametrize("provider", ["langsearch", "doubao"])
def test_cas_rotation_clear_and_child_inherit_freeze_in_one_transaction(
    configured, tmp_path, provider
):
    store, repo, secrets, app = configured
    save(app, provider)
    run, _ = store.create_run(session(store, tmp_path), "first")
    original = repo.snapshot(run["id"])
    queued, _ = store.enqueue_run(run["sessionId"], "queued")
    with pytest.raises(ApplicationError) as conflict:
        save(app, "deepseek", revision=0)
    assert conflict.value.code == "WEB_SEARCH_CONFIG_CONFLICT"
    save(app, provider, key="rotated-search-key")
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
            credential_changes=[{"provider": provider, "action": "clear"}],
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
    assert app.read().credential_values.langsearch == ""
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
        assert restored.credential_values.langsearch == "fixture-search-key"
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


@pytest.mark.parametrize("provider", ["deepseek", "langsearch", "doubao"])
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
                "ResponseMetadata": {},
                "Result": {
                    "ResultCount": len(hits),
                    "WebResults": [
                        {
                            "Title": hit.get("title", ""),
                            "Url": hit["url"],
                            "Summary": hit.get("summary"),
                            "Content": hit.get("content"),
                        }
                        for hit in hits
                    ],
                },
            }
            if provider == "doubao"
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
        else "https://open.feedcoopapi.com/search_api/web_search"
        if provider == "doubao"
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
@pytest.mark.parametrize("provider", ["deepseek", "langsearch", "doubao"])
def test_http_failure_never_retries_or_follows_redirects(status, code, provider):
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
            snapshot(provider), "key", "query", 5, transport=httpx.MockTransport(handle)
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


@pytest.mark.parametrize("mode", ["manual", "auto_review", "full_access"])
@pytest.mark.parametrize("provider", ["langsearch", "doubao"])
def test_real_runtime_search_needs_no_approval_but_keeps_intent_and_shell_network_boundary(
    tmp_path, monkeypatch, mode, provider
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

    def unexpected_review(*_args, **_kwargs):
        pytest.fail("Configured web search must not request model review")

    monkeypatch.setattr(
        "eidos_runtime.runtime.approval.review_approval", unexpected_review
    )
    model = ScriptedModel(
        [
            ModelResponse(
                tool_calls=(
                    ModelToolCall("search-call", "web_search", {"query": "query"}),
                )
            ),
            ModelResponse(
                tool_calls=(
                    ModelToolCall(
                        "search-again", "web_search", {"query": "next query"}
                    ),
                )
            ),
            ModelResponse(text="已完成。"),
        ]
    )
    server, output = start_server(tmp_path, model)
    try:
        save(server._web_search_application(), provider)
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
        wait_for(
            lambda: server.store.read_run(run_id)["status"]
            in {"succeeded", "failed", "interrupted"},
            output,
        )
        assert server.store.read_run(run_id)["status"] == "succeeded", output.getvalue()
        assert calls == [
            (provider, "fixture-search-key", "query", 5),
            (provider, "fixture-search-key", "next query", 5),
        ]
        assert (
            server.store.connection.execute(
                "SELECT count(*) FROM durable_intents"
            ).fetchone()[0]
            == 2
        )
        assert (
            server.store.connection.execute(
                "SELECT count(*) FROM approvals"
            ).fetchone()[0]
            == 0
        )
        assert '"method": "item/requestApproval"' not in output.getvalue()
        assert server.store.run_permission_grants(run_id).network is None
        rows = server.store.connection.execute(
            "SELECT result_json FROM tool_calls WHERE tool_name='web_search'"
        ).fetchall()
        assert len(rows) == 2 and all(
            json.loads(row[0])["outcome"] == "success" for row in rows
        )
        assert "fixture-search-key" not in output.getvalue()
        assert "fixture-search-key" not in json.dumps(model.contexts, default=str)
    finally:
        server.close()


@pytest.mark.parametrize('decision', ['approve', 'reject'])
def test_legacy_pending_search_approval_recovers_after_restart(tmp_path, monkeypatch, decision):
    from dataclasses import replace
    import threading

    from eidos_runtime.infrastructure.web_search import SearchPage
    from eidos_runtime.runtime.approval import ApprovalDecision
    from eidos_runtime.runtime.engine import RuntimeEngine
    from eidos_runtime.runtime.tool_execution import ToolExecutionController
    from eidos_runtime.tools.web_search import web_search_entry

    class ProcessCrash(BaseException):
        pass

    calls = []

    async def search(*_args):
        calls.append('search')
        return SearchPage(results=[], truncated=False, retrieved_at='2026-10-22T00:00:00+00:00')

    monkeypatch.setattr('eidos_runtime.tools.web_search.search', search)
    data = tmp_path / 'data'
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    store = SessionStore(data)
    store.initialize()
    kernel = RuntimeAsyncKernel()
    kernel.start()
    try:
        save(WebSearchApplication(WebSearchRepository(store.database), SecretFiles(data / 'web-search-secrets')))
        session = store.create_session(str(workspace))
        run, _ = store.create_run(session['id'], 'Search query')
        model = ScriptedModel([ModelResponse(tool_calls=(ModelToolCall('search', 'web_search', {'query': 'query'}),))])
        cancel = threading.Event()

        def legacy_authorize(controller, *, item, prepared):
            return controller.authorize_side_effect(run_id=run['id'], item=item, prepared=prepared, cancel=cancel)

        def legacy_entry(*args):
            entry = web_search_entry(*args)
            spec = entry.spec.model_copy(update={'approval_required': True})
            return replace(entry, spec=spec, runtime=replace(entry.runtime, spec=spec), execution_policy=None)

        def crash(*_args):
            raise ProcessCrash()

        with monkeypatch.context() as legacy:
            legacy.setattr(ToolExecutionController, 'authorize_workspace_side_effect', legacy_authorize)
            legacy.setattr('eidos_runtime.tools.web_search.web_search_entry', legacy_entry)
            with pytest.raises(ProcessCrash):
                RuntimeEngine(store, model, lambda _message: None,
                    request_approval=crash, async_kernel=kernel).run(run['id'], cancel)
        assert not calls
        pending = store.typed_runtime_repository().read_pending_approval(run['id'])
        store.close()
        store = SessionStore(data)
        store.initialize()
        requests = []
        RuntimeEngine(store, ScriptedModel([ModelResponse(text='Done.')]), lambda _message: None,
            request_approval=lambda request, _cancel: requests.append(request) or ApprovalDecision(decision),
            async_kernel=kernel).run(run['id'], threading.Event())
        assert store.read_run(run['id'])['status'] == 'succeeded'
        assert len(requests) == 1
        assert calls == (['search'] if decision == 'approve' else [])
        rows = store.connection.execute('SELECT id,decision FROM approvals').fetchall()
        assert [(row['id'], row['decision']) for row in rows] == [(pending.id, decision)]
    finally:
        kernel.close()
        store.close()


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


@pytest.mark.parametrize("provider", ["langsearch", "doubao"])
def test_secret_cleanup_retains_queued_runs_and_removes_unreferenced_versions(
    configured, tmp_path, provider
):
    store, repo, secrets, app = configured
    save(app, provider)
    run, _ = store.create_run(session(store, tmp_path), "retain")
    original = repo.snapshot(run["id"])
    orphan = secrets.write("orphan")
    save(app, provider, key="replacement")
    replacement = repo.read().credentials.get(provider)
    assert secrets.read(replacement) == "replacement"
    assert not (secrets.directory / orphan).exists()
    assert secrets.read(original.credential_ref) == "fixture-search-key"
    with store.database.transaction() as transaction:
        transaction.execute(
            "UPDATE runs SET status='canceled' WHERE id=?", (run["id"],)
        )
    save(app, provider, key=None)
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


@pytest.mark.parametrize("mode", ["manual", "auto_review", "full_access"])
@pytest.mark.parametrize("provider", ["langsearch", "doubao"])
def test_cancel_cleans_owned_network_task_and_late_result_cannot_replace_cancellation(
    tmp_path, monkeypatch, mode, provider
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
        save(server._web_search_application(), provider)
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
                approvalMode=mode,
                fullAccessConfirmation="full-access-v1"
                if mode == "full_access"
                else None,
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


def test_doubao_query_schema_filters_and_legacy_contract_hashes(tmp_path):
    assert DoubaoSearchInput(query="搜" * 100).query
    with pytest.raises(ValidationError):
        DoubaoSearchInput(query="搜" * 101)
    with pytest.raises(ValidationError):
        DoubaoSearchOptions(exclude_domains=[f"site{i}.com" for i in range(6)])
    with pytest.raises(ValidationError):
        DoubaoSearchOptions(freshness="2026-02-31")
    with pytest.raises(ValidationError):
        DoubaoSearchOptions(
            include_domains=["example.com"], exclude_domains=["EXAMPLE.COM"]
        )
    body = request_payload(
        snapshot(
            "doubao",
            freshness="oneWeek",
            includeDomains=["Example.com"],
            excludeDomains=["spam.org"],
            includePageContent=True,
        ),
        "查询",
        5,
    )
    assert body == {
        "Query": "查询",
        "SearchType": "web",
        "Count": 5,
        "Filter": {
            "NeedUrl": True,
            "TimeRange": "OneWeek",
            "Sites": "example.com",
            "BlockHosts": "spam.org",
        },
        "QueryControl": {"QueryRewrite": False},
        "ContentFormats": "text",
    }
    assert "TimeRange" not in request_payload(snapshot("doubao"), "查询", 5)["Filter"]
    for freshness, expected in [
        ("2026-10-01", "2026-10-01..2026-10-01"),
        ("2026-10-01..2026-10-20", "2026-10-01..2026-10-20"),
    ]:
        assert (
            request_payload(snapshot("doubao", freshness=freshness), "查询", 5)[
                "Filter"
            ]["TimeRange"]
            == expected
        )
    secrets = SecretFiles(tmp_path / "secrets")
    # Captured before adding Doubao. Existing pending Runs must keep these contracts.
    for provider, expected in {
        "deepseek": "89ac96b4be6cd34989613f64f750ed1f3f65cb4bcca7f67723ec24a7ca91f79c",
        "langsearch": "dc324dc44c1edaf05e377acd0861013cb4a5741ca9ece76742eb0d259e7ad52c",
    }.items():
        assert (
            web_search_entry(snapshot(provider), secrets).provenance.content_hash
            == expected
        )
    entry = web_search_entry(snapshot("doubao"), secrets)
    assert entry.spec.input_schema["properties"]["query"]["maxLength"] == 100


@pytest.mark.parametrize(
    "code,expected",
    [
        (10400, "web_search_request_failed"),
        (10401, "web_search_auth_failed"),
        (10402, "web_search_access_denied"),
        (10403, "web_search_access_denied"),
        (10406, "web_search_access_denied"),
        (10408, "web_search_access_denied"),
        (10409, "web_search_access_denied"),
        (10410, "web_search_access_denied"),
        (10412, "web_search_access_denied"),
        (10500, "web_search_unavailable"),
        (700429, "web_search_rate_limited"),
        (99999, "web_search_request_failed"),
    ],
)
@pytest.mark.parametrize("status", [200, 400])
def test_doubao_business_errors_never_leak_or_retry(code, expected, status):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            status,
            json={
                "ResponseMetadata": {
                    "Error": {
                        "Code": str(code),
                        "CodeN": code,
                        "Message": "fixture-secret",
                    }
                },
                "Result": None,
            },
        )

    async def run():
        return await search(
            snapshot("doubao"), "key", "查询", 5, transport=httpx.MockTransport(handle)
        )

    with pytest.raises(SearchFailure) as raised:
        anyio.run(run)
    assert raised.value.code == expected and raised.value.sent
    assert "fixture-secret" not in raised.value.message and len(requests) == 1


@pytest.mark.parametrize(
    "body,expected",
    [
        (
            {"ResponseMetadata": {}, "Result": {"ResultCount": 0, "WebResults": None}},
            None,
        ),
        (
            {"ResponseMetadata": {}, "Result": {"ResultCount": 0, "WebResults": []}},
            None,
        ),
        ({"ResponseMetadata": {}, "Result": None}, "web_search_invalid_response"),
        (
            {"ResponseMetadata": {}, "Result": {"ResultCount": 1}},
            "web_search_invalid_response",
        ),
        (
            {
                "ResponseMetadata": {},
                "Result": {
                    "ResultCount": 1,
                    "WebResults": [{"Url": "file:///etc/passwd"}],
                },
            },
            "web_search_invalid_response",
        ),
    ],
)
def test_doubao_empty_and_invalid_responses(body, expected):
    async def run():
        return await search(
            snapshot("doubao"),
            "key",
            "查询",
            5,
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body)),
        )

    if expected:
        with pytest.raises(SearchFailure) as raised:
            anyio.run(run)
        assert raised.value.code == expected
    else:
        assert anyio.run(run).results == []


@pytest.mark.parametrize(
    "include_content,excerpt,kind",
    [(False, "摘要[redacted]", "summary"), (True, "正文", "text")],
)
def test_doubao_prefers_summary_or_content_and_preserves_publish_time(
    include_content, excerpt, kind
):
    body = {
        "ResponseMetadata": {},
        "Result": {
            "ResultCount": 1,
            "WebResults": [
                {
                    "Title": "标题",
                    "Url": "https://example.com",
                    "Snippet": "片段",
                    "Summary": "摘要fixture-key",
                    "Content": "正文",
                    "PublishTime": "2026-10-26T10:00:00+08:00",
                }
            ],
        },
    }

    async def run():
        return await search(
            snapshot("doubao", includePageContent=include_content),
            "fixture-key",
            "查询",
            5,
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body)),
        )

    page = anyio.run(run)
    assert page.results[0].excerpt == excerpt and page.results[0].content_kind == kind
    assert page.results[0].published_at == "2026-10-26T10:00:00+08:00"
    assert page.usage is None


def test_all_three_credentials_save_without_cleanup_deleting_current_key(configured):
    _, repo, secrets, app = configured
    result = app.save(
        WebSearchSaveRequest(
            expected_revision=0,
            config=WebSearchConfig(default_provider="doubao"),
            credential_changes=[
                {
                    "provider": provider,
                    "action": "set",
                    "value": f"fixture-{provider}-key",
                }
                for provider in ("deepseek", "langsearch", "doubao")
            ],
        )
    )
    assert result.credential_values.doubao == "fixture-doubao-key"
    assert "fixture-doubao-key" not in repr(result)
    for provider in ("deepseek", "langsearch", "doubao"):
        assert (
            secrets.read(repo.read().credentials.get(provider))
            == f"fixture-{provider}-key"
        )

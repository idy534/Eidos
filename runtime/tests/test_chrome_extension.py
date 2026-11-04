from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
from unittest.mock import patch

import anyio
import pytest

from eidos_runtime.application.browser import BrowserApplication
from eidos_runtime.application.errors import ApplicationInvalidParamsError
from eidos_runtime.db.schema import SCHEMA_VERSION, V49_SCHEMA_VERSION
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.infrastructure.browser_runtime import BrowserRuntime
from eidos_runtime.infrastructure.chrome_extension import (
    BROKER_SOCKET_NAME,
    MAX_CONNECT_PAGES_PER_CONNECTION,
    PLAYWRIGHT_EXTENSION_ID,
    ConnectPageBroker,
    ExtensionTokenInvalid,
    connect_url_rejection,
    extension_presence,
    normalize_extension_token,
)
from eidos_runtime.models.browser import (
    BrowserExtensionTokenClearRequest,
    BrowserExtensionTokenRequest,
    BrowserStatusRequest,
    BrowserTargetRequest,
)
from eidos_runtime.persistence.browser_grants import BrowserGrantRepository
from eidos_runtime.runtime import browser_launch
from eidos_runtime.runtime.browser_launch import browser_client_name, resolve_browser_launch

TOKEN = "Abcdefghijklmnopqrstuvwxyz0123456789-_ABCDE"
RELAY = "ws%3A%2F%2F127.0.0.1%3A50123%2Fextension%2F5733f635-0f0a-41ef-9972-305b283803db"
CONNECT = f"chrome-extension://{PLAYWRIGHT_EXTENSION_ID}/connect.html?mcpRelayUrl={RELAY}"


# --- token, presence and connect page -------------------------------------------------

@pytest.mark.parametrize("pasted", [
    TOKEN, f"  {TOKEN}\n", f"PLAYWRIGHT_MCP_EXTENSION_TOKEN={TOKEN}",
    f"export PLAYWRIGHT_MCP_EXTENSION_TOKEN='{TOKEN}'", f'PLAYWRIGHT_MCP_EXTENSION_TOKEN="{TOKEN}"',
])
def test_token_accepts_what_the_extension_copy_button_copies(pasted):
    assert normalize_extension_token(pasted) == TOKEN


@pytest.mark.parametrize("pasted", ["", "short", "PLAYWRIGHT_MCP_EXTENSION_TOKEN=", TOKEN + " extra", "a" * 200, TOKEN + "=="])
def test_token_rejects_anything_else(pasted):
    with pytest.raises(ExtensionTokenInvalid):
        normalize_extension_token(pasted)


def test_presence_finds_the_extension_in_any_profile(tmp_path):
    chrome = tmp_path / "Chrome"
    assert extension_presence(chrome) == "missing"
    (chrome / "Default").mkdir(parents=True)
    (chrome / "Default" / "Preferences").write_text(json.dumps({"extensions": {"settings": {"other": {"a": 1}}}}))
    assert extension_presence(chrome) == "missing"
    (chrome / "Profile 2").mkdir()
    (chrome / "Profile 2" / "Secure Preferences").write_text(
        json.dumps({"extensions": {"settings": {PLAYWRIGHT_EXTENSION_ID: {"state": 1}}}}),
    )
    assert extension_presence(chrome) == "installed"


def test_presence_is_unknown_when_profile_files_cannot_be_read(tmp_path):
    chrome = tmp_path / "Chrome"
    (chrome / "Default").mkdir(parents=True)
    (chrome / "Default" / "Preferences").write_text("{not json")
    assert extension_presence(chrome) == "unknown"
    (chrome / "Default" / "Extensions" / PLAYWRIGHT_EXTENSION_ID).mkdir(parents=True)
    assert extension_presence(chrome) == "installed"


@pytest.mark.parametrize("url,reason", [
    (CONNECT + "&client=%7B%22name%22%3A%22Eidos+%C2%B7+%E4%BC%9A%E8%AF%9D%22%7D&protocolVersion=2&token=x", None),
    ("https://evil.test/connect.html?mcpRelayUrl=" + RELAY, "not_connect_page"),
    (CONNECT.replace(PLAYWRIGHT_EXTENSION_ID, "a" * 32), "not_connect_page"),
    (CONNECT.replace("127.0.0.1", "10.0.0.5"), "relay_not_loopback"),
    (CONNECT.replace("%2Fextension%2F", "%2Fcdp%2F"), "relay_not_loopback"),
    (CONNECT + "&mcpRelayUrl=" + RELAY, "unexpected_parameters"),
    (CONNECT + "&debug=1", "unexpected_parameters"),
    (CONNECT + "#x", "not_connect_page"),
    (CONNECT + "&client=" + "a" * 5000, "too_long"),
])
def test_connect_page_requests_are_validated(url, reason):
    assert connect_url_rejection(url) == reason


def test_broker_opens_only_valid_connect_pages_and_stops_at_its_limit():
    import tempfile

    opened: list[str] = []
    # Unix socket paths are limited to 104 bytes; pytest's tmp_path is deeper.
    short = Path(tempfile.mkdtemp(prefix="eb-", dir="/private/tmp"))

    async def opener(url: str) -> None:
        opened.append(url)

    async def main() -> None:
        path = short / BROKER_SOCKET_NAME
        listener = await anyio.create_unix_listener(path)
        async with listener, anyio.create_task_group() as group:
            group.start_soon(ConnectPageBroker(opener).serve, listener)
            for url in ["https://evil.test/", "", "x" * 9000, *[CONNECT] * (MAX_CONNECT_PAGES_PER_CONNECTION + 2)]:
                async with await anyio.connect_unix(path) as stream:
                    try:
                        await stream.send(url.encode() + b"\n")
                    except anyio.BrokenResourceError:
                        pass  # The broker stops reading an oversized request.
            with anyio.fail_after(5):
                while len(opened) < MAX_CONNECT_PAGES_PER_CONNECTION:
                    await anyio.sleep(0.01)
            await anyio.sleep(0.1)
            group.cancel_scope.cancel()

    try:
        anyio.run(main)
    finally:
        (short / BROKER_SOCKET_NAME).unlink(missing_ok=True)
        short.rmdir()
    assert opened == [CONNECT] * MAX_CONNECT_PAGES_PER_CONNECTION


# --- Run-start target resolution ------------------------------------------------------

class _Host:
    def endpoint(self) -> str:
        return "http://127.0.0.1:9333"

    def ensure(self, _cancel: object) -> str:
        return self.endpoint()

    def status(self):
        class Status:
            chrome_installed = True
        return Status()


@pytest.fixture
def store(tmp_path):
    store = SessionStore(tmp_path / "data")
    store.initialize()
    yield store
    store.close()


def _resolve(store, presence="installed", title="修复登录问题"):
    runtime = BrowserRuntime(node=Path("/bin/node"), entry=Path("/cli.js"), read_roots=(Path("/a"), Path("/b")))
    with patch.object(browser_launch, "resolve_browser_runtime", lambda _manager: runtime):
        return resolve_browser_launch(store, _Host(), None, session_title=title, presence=lambda: presence)


def _application(store):
    class Events:
        def publish(self, _mutation):
            pass
    return BrowserApplication(store, Events(), lambda: None, lambda: None, presence=lambda: "installed")


def test_default_target_falls_back_to_the_eidos_browser_until_a_token_is_saved(store):
    launch = _resolve(store)
    assert (launch.mode, launch.profile, launch.variables["BROWSER_CDP_ENDPOINT"]) == ("cdp", "default", "http://127.0.0.1:9333")
    _application(store).set_extension_token(BrowserExtensionTokenRequest(token=TOKEN))
    launch = _resolve(store)
    assert (launch.mode, launch.profile, launch.variables["BROWSER_CDP_ENDPOINT"]) == ("extension", "user", "")
    assert launch.environment == {"PLAYWRIGHT_MCP_EXTENSION_TOKEN": TOKEN}
    assert launch.client_name == "Eidos · 修复登录问题"
    assert launch.ensure_browser is None
    assert TOKEN not in repr(launch)


def test_extension_missing_from_chrome_falls_back_and_unknown_still_tries(store):
    _application(store).set_extension_token(BrowserExtensionTokenRequest(token=TOKEN))
    assert _resolve(store, presence="missing").mode == "cdp"
    assert _resolve(store, presence="unknown").mode == "extension"


def test_an_explicit_eidos_target_ignores_a_saved_token(store):
    application = _application(store)
    application.set_extension_token(BrowserExtensionTokenRequest(token=TOKEN))
    application.set_target(BrowserTargetRequest(target="eidos"))
    assert _resolve(store).mode == "cdp"


def test_client_name_is_bounded_and_single_line():
    assert browser_client_name(None) == "Eidos"
    assert browser_client_name("  修复\n登录  ") == "Eidos · 修复 登录"
    assert browser_client_name("x" * 50) == "Eidos · " + "x" * 20 + "…"


# --- settings application -------------------------------------------------------------

def test_token_is_stored_outside_sqlite_and_reported_only_as_configured(store, tmp_path):
    application = _application(store)
    assert application.status(BrowserStatusRequest()).effective_target == "eidos"
    response = application.set_extension_token(BrowserExtensionTokenRequest(token=f"PLAYWRIGHT_MCP_EXTENSION_TOKEN={TOKEN}"))
    assert response.configured
    status = application.status(BrowserStatusRequest())
    assert (status.target, status.effective_target, status.extension_configured, status.extension_presence) == (
        "chrome_extension", "chrome_extension", True, "installed",
    )
    assert TOKEN not in status.model_dump_json()
    database = Path(store.data_directory) / "state.sqlite"
    store.connection.execute("PRAGMA wal_checkpoint(FULL)")
    assert TOKEN.encode() not in database.read_bytes()
    secrets = Path(store.data_directory) / "browser-secrets"
    assert [path.read_text() for path in secrets.iterdir()] == [TOKEN]
    assert oct(secrets.stat().st_mode & 0o777) == "0o700"
    # Replacing the token leaves exactly one secret file.
    application.set_extension_token(BrowserExtensionTokenRequest(token=TOKEN[::-1]))
    assert [path.read_text() for path in secrets.iterdir()] == [TOKEN[::-1]]
    cleared = application.clear_extension_token(BrowserExtensionTokenClearRequest())
    assert not cleared.configured
    assert list(secrets.iterdir()) == []
    assert application.status(BrowserStatusRequest()).effective_target == "eidos"


def test_invalid_token_is_a_stable_invalid_params_error(store):
    with pytest.raises(ApplicationInvalidParamsError) as raised:
        _application(store).set_extension_token(BrowserExtensionTokenRequest(token="nope"))
    assert raised.value.code == "BROWSER_EXTENSION_TOKEN_INVALID"
    assert BrowserGrantRepository(store.database).extension_credential() is None


def test_pairing_or_switching_how_chrome_is_reached_asks_each_session_again(store, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = store.create_session(str(workspace))
    repository = BrowserGrantRepository(store.database)

    def grant() -> None:
        repository.grant(session_id=session["id"], plugin_id="eidos-browser", plugin_version="1.0.0",
                         plugin_hash="h", source_approval_id=None, profile="user")

    application = _application(store)
    grant()
    assert application.set_extension_token(BrowserExtensionTokenRequest(token=TOKEN)).revoked_grants == 1
    grant()
    assert application.set_target(BrowserTargetRequest(target="user_chrome")).revoked_grants == 1
    grant()
    assert application.set_target(BrowserTargetRequest(target="user_chrome")).revoked_grants == 0
    assert application.clear_extension_token(BrowserExtensionTokenClearRequest()).revoked_grants == 1
    status = application.status(BrowserStatusRequest(session_id=session["id"]))
    assert (status.effective_target, status.granted) == ("user_chrome", False)


# --- schema v50 -----------------------------------------------------------------------

def _downgrade_to_v49(data: Path, target: str | None, extra_sql: str = "") -> None:
    store = SessionStore(data)
    store.initialize()
    store.close()
    connection = sqlite3.connect(data / "state.sqlite")
    rows = f"INSERT INTO browser_settings VALUES (1, '{target}', 1);" if target else ""
    connection.executescript(
        "DROP TABLE browser_settings;"
        "CREATE TABLE browser_settings (id INTEGER PRIMARY KEY CHECK (id = 1), "
        "target TEXT NOT NULL CHECK (target IN ('eidos', 'user_chrome')), updated_at INTEGER NOT NULL);"
        + rows + extra_sql + f"PRAGMA user_version={V49_SCHEMA_VERSION};"
    )
    connection.close()


@pytest.mark.parametrize("before,after", [(None, "chrome_extension"), ("eidos", "chrome_extension"), ("user_chrome", "user_chrome")])
def test_v49_database_upgrades_browser_settings(tmp_path, before, after):
    data = tmp_path / "data"
    _downgrade_to_v49(data, before)
    store = SessionStore(data)
    store.initialize()
    try:
        assert store.connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 50
        repository = BrowserGrantRepository(store.database)
        assert repository.target() == after
        assert repository.extension_credential() is None
    finally:
        store.close()


def test_failed_v50_migration_rolls_back_to_v49(tmp_path):
    data = tmp_path / "data"
    # A leftover table makes the rebuild fail inside the migration transaction.
    _downgrade_to_v49(data, "user_chrome", "CREATE TABLE browser_settings_v50 (marker TEXT);")
    store = SessionStore(data)
    store.initialize()
    store.close()
    connection = sqlite3.connect(data / "state.sqlite")
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == V49_SCHEMA_VERSION
        assert connection.execute("SELECT target FROM browser_settings").fetchone()[0] == "user_chrome"
    finally:
        connection.close()


def test_secret_directory_is_never_inside_the_workspace_or_sqlite(store):
    assert os.path.basename(browser_launch.BROWSER_SECRETS_DIRECTORY) == "browser-secrets"

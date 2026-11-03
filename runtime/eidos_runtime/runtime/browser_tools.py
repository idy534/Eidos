"""Authorization inputs for the bundled browser tools.

ExternalToolHandler keeps the existing Validate -> Prepare -> Permission ->
Durable Intent -> Execute -> Verify chain. This module only decides which
browser tools need a Session grant or a per-call approval, and starts the
shared Chrome after authorization, inside the intent's execution window.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
from pathlib import Path
import threading

from eidos_runtime.domain.browser import BrowserToolClass, browser_tool_class
from eidos_runtime.extensions.browser_plugin import BrowserLaunch
from eidos_runtime.infrastructure.browser_host import BrowserUnavailable
from eidos_runtime.runtime.errors import tool_error

MAX_UPLOAD_PATHS = 16

BROWSER_ACCESS_WARNING = (
    "批准后，本会话中的后续任务都可以在 Eidos 浏览器里打开网页、点击和输入，并使用该浏览器中已登录的账号，"
    "直到你撤销授权。浏览器由所有会话共用，Agent 能看到其他任务打开的页面。"
    "购买、发送、发布或删除前，Agent 仍会先询问你；上传文件每次都需要批准。"
)
USER_CHROME_ACCESS_WARNING = (
    "批准后，本会话中的后续任务可以操作你日常使用的 Google Chrome，并使用其中所有已登录的账号，"
    "包括邮箱、支付和工作账号，直到你撤销授权。Chrome 每次连接时还会请你确认。"
    "购买、发送、发布或删除前，Agent 仍会先询问你；上传文件每次都需要批准。"
)
BROWSER_UPLOAD_WARNING = "批准后，这些文件会发送给当前网页所在的网站。"


class BrowserUploadRejected(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class BrowserCall:
    tool_class: BrowserToolClass
    launch: BrowserLaunch


def browser_call(implementation: object) -> BrowserCall | None:
    connection = getattr(implementation, "connection", None)
    launch = getattr(connection, "browser_launch", None)
    remote_name = getattr(implementation, "remote_name", None)
    if not isinstance(launch, BrowserLaunch) or not isinstance(remote_name, str):
        return None
    tool_class = browser_tool_class(remote_name)
    return BrowserCall(tool_class, launch) if tool_class is not None else None


def chrome_missing(call_name: str, browser: BrowserCall) -> dict[str, object] | None:
    """Fail before approval when Chrome is absent; the user decided not to fall back."""

    installed = browser.launch.chrome_installed
    if installed is None or installed():
        return None
    return tool_error(
        call_name, "browser_unavailable",
        "Google Chrome is not installed. Ask the user to install Google Chrome, then retry.",
    )


def with_browser(
    call_name: str,
    browser: BrowserCall,
    cancel: threading.Event,
    execute: Callable[[], dict[str, object]],
) -> Callable[[], dict[str, object]]:
    """Start the shared Chrome only once the call is authorized and its intent committed."""

    def run() -> dict[str, object]:
        ensure = browser.launch.ensure_browser
        if ensure is not None:
            try:
                ensure(cancel)
            except BrowserUnavailable as error:
                return tool_error(call_name, error.code, _UNAVAILABLE_SUMMARIES.get(
                    error.code, "The Eidos browser could not be started.",
                ))
        return execute()

    return run


def upload_paths(arguments: dict[str, object], roots: tuple[Path, ...]) -> tuple[str, ...]:
    """Resolve upload paths; only regular files inside the Workspace roots qualify."""

    raw = arguments.get("paths")
    if not isinstance(raw, list) or not raw or len(raw) > MAX_UPLOAD_PATHS:
        raise BrowserUploadRejected("browser_upload_invalid")
    canonical_roots = tuple(Path(os.path.realpath(root)) for root in roots)
    resolved: list[str] = []
    for value in raw:
        if not isinstance(value, str) or not value or "\0" in value:
            raise BrowserUploadRejected("browser_upload_invalid")
        candidate = Path(value) if os.path.isabs(value) else canonical_roots[0] / value
        real = Path(os.path.realpath(candidate))
        if not any(real == root or real.is_relative_to(root) for root in canonical_roots):
            raise BrowserUploadRejected("browser_upload_outside_workspace")
        if not real.is_file():
            raise BrowserUploadRejected("browser_upload_not_a_file")
        resolved.append(str(real))
    return tuple(resolved)


def browser_access_description(
    call_name: str, arguments: dict[str, object], details: dict[str, object], profile: str = "default",
) -> dict[str, object]:
    user = profile == "user"
    return {
        "kind": "browser_access",
        "summary": "允许本会话使用你的 Chrome" if user else "允许本会话使用 Eidos 浏览器",
        "toolName": call_name,
        "arguments": arguments,
        "grantScope": "session",
        "profile": profile,
        "access": "interact",
        "riskWarning": USER_CHROME_ACCESS_WARNING if user else BROWSER_ACCESS_WARNING,
        # The user's everyday accounts need the user's own decision.
        **({"requiresHumanConfirmation": True} if user else {}),
        **details,
    }


def browser_upload_description(
    call_name: str, arguments: dict[str, object], paths: tuple[str, ...], details: dict[str, object],
) -> dict[str, object]:
    return {
        "kind": "external_tool",
        "summary": "向网页上传本地文件",
        "toolName": call_name,
        "arguments": {**arguments, "paths": list(paths)},
        "riskWarning": BROWSER_UPLOAD_WARNING,
        **details,
    }


_UNAVAILABLE_SUMMARIES = {
    "browser_unavailable": "Google Chrome is not installed. Ask the user to install Google Chrome, then retry.",
    "browser_profile_in_use": (
        "The Eidos Chrome profile is open in a Chrome window that Eidos cannot control. "
        "Ask the user to close that window, then retry."
    ),
    "browser_start_failed": "The Eidos browser could not be started. Retry once; if it fails again, tell the user.",
    "browser_start_canceled": "Starting the Eidos browser was canceled.",
    "browser_attach_unavailable": (
        "The user's Chrome is no longer accepting remote debugging. Ask the user to keep Chrome open with "
        "remote debugging enabled at chrome://inspect/#remote-debugging, then retry."
    ),
}

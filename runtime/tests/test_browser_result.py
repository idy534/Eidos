"""Samples are Playwright MCP 0.0.84 results captured from a real Eidos Run."""
from eidos_runtime.domain.browser_result import browser_view, clean_browser_text
from eidos_runtime.protocol.tool_text import project_tool_text
from eidos_runtime.tools.contracts import project_tool_result

OUT = "../../../../../private/tmp/eidos-mcp-wyuhbjuc/tmp/browser-output"

TYPE = f"""### Ran Playwright code
```js
await page.getByRole('combobox', {{ name: '搜索' }}).fill('哔哩哔哩');
await page.getByRole('combobox', {{ name: '搜索' }}).press('Enter');
```
### Open tabs
- 0: [](about:blank)
- 1: (current) [哔哩哔哩 - Google 搜索](https://www.google.com/search?q=bili)
### Page
- Page URL: https://www.google.com/search?q=bili
- Page Title: 哔哩哔哩 - Google 搜索
- Console: 1 errors, 0 warnings
### Snapshot
- [Snapshot]({OUT}/page-2026-11-04T04-17-27-560Z.yml)
### Events
- New console entries: {OUT}/console-2026-11-04T04-17-17-941Z.log#L2-L3"""

SCREENSHOT = f"""### Result
- [Screenshot of viewport]({OUT}/page-2026-11-04T04-18-38-969Z.png)
### Ran Playwright code
```js
// Screenshot viewport and save it as {OUT}/page-2026-11-04T04-18-38-969Z.png
await page.screenshot({{
  path: '{OUT}/page-2026-11-04T04-18-38-969Z.png',
  scale: 'css',
  type: 'png'
}});
```"""

SNAPSHOT = """### Page
- Page URL: https://www.bilibili.com/zh-Hans
- Page Title: bilibili
### Snapshot
```yaml
- generic [ref=f7e4]:
  - link "首页" [ref=f7e9]
```"""


def test_private_output_links_become_plain_notes():
    cleaned = clean_browser_text(TYPE + "\n" + SCREENSHOT)
    assert "browser-output" not in cleaned
    assert "private/tmp" not in cleaned
    assert "Snapshot (not retained; call browser_snapshot to read the page)" in cleaned
    assert "New console entries: (not retained; call browser_console_messages to read them)" in cleaned
    assert "Screenshot of viewport (attached to this result)" in cleaned
    assert "path: '(attached to this result)'" in cleaned


def test_view_extracts_page_tabs_and_console():
    view = browser_view(TYPE)
    assert view["page"] == {"url": "https://www.google.com/search?q=bili", "title": "哔哩哔哩 - Google 搜索"}
    assert view["console"] == {"errors": 1, "warnings": 0}
    assert view["tabs"] == [
        {"index": 0, "current": False, "title": "", "url": "about:blank"},
        {"index": 1, "current": True, "title": "哔哩哔哩 - Google 搜索", "url": "https://www.google.com/search?q=bili"},
    ]
    assert "code" not in view
    assert "snapshotLines" not in view


def test_inline_snapshot_reports_its_size_and_result_is_cleaned():
    assert browser_view(SNAPSHOT)["snapshotLines"] == 2
    assert browser_view(SCREENSHOT)["result"] == "- Screenshot of viewport (attached to this result)"


def test_unknown_text_has_no_view():
    assert browser_view("plain text without sections") is None


def _result(name, text):
    return {
        "schemaVersion": 1, "toolName": name, "outcome": "success", "code": "ok", "summary": "MCP tool completed",
        "data": {"text": text, "isError": False}, "sideEffectsMayExist": True, "reconciliationRequired": False,
    }


def test_projection_cleans_model_text_and_adds_the_view_only_for_the_feed():
    canonical = _result("mcp__browser__browser_type", TYPE)
    projection = project_tool_result("mcp__browser__browser_type", canonical)
    assert canonical["data"]["text"] == TYPE
    assert "browser-output" not in projection.model_result["data"]["text"]
    assert "browser" not in projection.model_result["data"]
    assert projection.ui_result["data"]["browser"]["page"]["title"] == "哔哩哔哩 - Google 搜索"


def test_third_party_servers_are_left_unchanged():
    canonical = _result("mcp__github__create_issue", TYPE)
    projection = project_tool_result("mcp__github__create_issue", canonical)
    assert projection.ui_result["data"] == canonical["data"]


def test_paged_results_keep_attachments_and_the_page_view():
    import json

    ui = {**_result("mcp__browser__browser_snapshot", "x" * 70_000),
          "attachments": [{"index": 0, "kind": "image", "mime": "image/png", "bytes": 10}]}
    ui["data"]["browser"] = {"page": {"url": "https://a.test/", "title": "A"}}
    item = project_tool_text({"kind": "tool_call", "toolCall": {"toolName": "mcp__browser__browser_snapshot", "resultJson": json.dumps(ui)}})
    shown = json.loads(item["toolCall"]["resultJson"])
    assert shown["attachments"] == ui["attachments"]
    assert shown["data"] == {"truncated": True, "browser": {"page": {"url": "https://a.test/", "title": "A"}}}
    assert item["toolCall"]["resultHash"]


def test_shared_tool_content_fixture_matches_the_python_dtos():
    import json
    from pathlib import Path

    from eidos_runtime.protocol.methods import ToolContentReadRequestDto, ToolContentReadResponseDto
    from eidos_runtime.protocol.schemas import ItemDto

    fixture = json.loads((Path(__file__).parents[2] / "protocol/fixtures/tool-content-v1.json").read_text())
    item = ItemDto.model_validate(fixture["item"])
    request = ToolContentReadRequestDto.model_validate(fixture["contentRequest"])
    assert request.tool_call_id == item.tool_call.id and request.sha256 is None
    response = ToolContentReadResponseDto.model_validate(fixture["content"])
    assert response.complete and response.next_offset == response.size_bytes
    assert json.loads(item.tool_call.result_json)["attachments"][0]["index"] == request.index
    assert item.tool_call.provenance["displayTitle"] == "Take a screenshot"

import mcp.types as mcp_types

from eidos_runtime.extensions.mcp import _display_title
from eidos_runtime.tools.registry import ToolProvenance


def _tool(**values):
    return mcp_types.Tool(name="create_issue", inputSchema={"type": "object"}, **values)


def test_title_prefers_the_tool_title_then_the_annotation():
    assert _display_title(_tool(title="Create issue", annotations=mcp_types.ToolAnnotations(title="Other"))) == "Create issue"
    assert _display_title(_tool(annotations=mcp_types.ToolAnnotations(title="  Create\n issue "))) == "Create issue"
    assert _display_title(_tool()) is None
    assert _display_title(_tool(title="   ")) is None
    assert len(_display_title(_tool(title="x" * 500))) == 120


def test_provenance_accepts_and_bounds_the_display_title():
    base = {"kind": "mcp", "sourceId": "github:github", "sourceVersion": "1", "contentHash": "a" * 64}
    assert ToolProvenance.model_validate(base).display_title is None
    assert ToolProvenance.model_validate({**base, "displayTitle": "Create issue"}).display_title == "Create issue"

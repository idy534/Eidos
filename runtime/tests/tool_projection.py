"""Project a canonical tool result by tool name, the way a registered tool's projector does."""
from __future__ import annotations

from types import SimpleNamespace

from eidos_runtime.tools.contracts import PROJECTORS, ToolResultProjection

_POLICY_BY_TOOL = {
    "list_files": "list_files",
    "read_file": "read_file",
    "read_file_range": "read_file_range",
    "search_text": "search_text",
    "search_text_wait": "search_text",
    "write_file": "file_change",
    "apply_patch": "file_change",
    "delete_file": "file_change",
    "run_shell": "run_shell",
    "write_stdin": "run_shell",
    "control_shell": "run_shell",
    "skill_read": "skill_read",
    "skill_read_resource": "skill_resource",
    "skill_create": "skill_change",
    "skill_install": "skill_change",
    "tool_search": "tool_search",
    "view_image": "view_image",
}


def project_tool_result(tool_name: str, canonical_result: dict[str, object]) -> ToolResultProjection:
    policy_id = _POLICY_BY_TOOL.get(tool_name, "mcp" if tool_name.startswith("mcp__") else "generic")
    descriptor = SimpleNamespace(spec=SimpleNamespace(name=tool_name))
    return PROJECTORS[policy_id].project(descriptor, canonical_result)

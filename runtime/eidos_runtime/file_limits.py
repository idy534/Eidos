"""Resource budgets for file editing, independent of model context/UI previews.

The standalone commit helper owns the per-file ceiling. These are memory/CPU
bounds, not instructions about how a model should organize a user's files.
"""
from eidos_runtime.sandbox.file_commit_helper import MAX_FILE_BYTES

MAX_PATCH_BYTES = 8 * 1024 * 1024
# A JSON string can use six bytes to encode one ASCII control character.
MAX_PATCH_ARGUMENT_BYTES = 6 * MAX_PATCH_BYTES + 1024
MAX_PATCH_WORKING_BYTES = 4 * MAX_FILE_BYTES
MAX_PATCH_RESULT_BYTES = MAX_PATCH_WORKING_BYTES
INLINE_TOOL_TEXT_BYTES = 64 * 1024
TOOL_TEXT_PAGE_CHARACTERS = 16 * 1024


def function_argument_limit(tool_name: str) -> int:
    return MAX_PATCH_ARGUMENT_BYTES if tool_name == "apply_patch" else 64 * 1024


def tool_result_limit(tool_name: str) -> int:
    return MAX_PATCH_RESULT_BYTES if tool_name == "apply_patch" else 512 * 1024

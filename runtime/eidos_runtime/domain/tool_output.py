"""Historical results that may be paged back without replaying an operation."""

RECOVERABLE_RESULT_TOOLS = frozenset(
    {
        "read_file",
        "read_file_range",
        "list_files",
        "search_text",
        "search_text_wait",
        "run_shell",
    }
)

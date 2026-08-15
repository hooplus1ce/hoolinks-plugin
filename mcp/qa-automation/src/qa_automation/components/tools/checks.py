from fastmcp.tools import tool


@tool(
    title="Text: Check String Length",
    description="Validate that a string does not exceed a maximum length. "
    "Returns OK/FAIL verdict with character counts for QA assertions.",
    tags={"qa", "text"},
)
def check_string_length(text: str, max_length: int = 100) -> str:
    """Validate that a string does not exceed a maximum length."""
    length = len(text)
    if length <= max_length:
        return f"OK: {length} characters (max {max_length})"
    return f"FAIL: {length} characters exceeds max {max_length}"

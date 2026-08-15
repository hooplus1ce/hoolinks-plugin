from fastmcp.prompts import prompt


@prompt
def test_case_review(test_case: str) -> str:
    """Generate a prompt for reviewing a test case."""
    return f"Review this test case for clarity and coverage:\n\n{test_case}"

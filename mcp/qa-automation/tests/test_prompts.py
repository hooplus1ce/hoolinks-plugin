from __future__ import annotations

import pytest
from fastmcp import Client

from qa_automation.server import mcp


@pytest.fixture
async def client() -> Client:
    async with Client(transport=mcp) as c:
        yield c


async def test_scm_login_prompt_registered(client: Client) -> None:
    prompts = await client.list_prompts()
    names = {p.name for p in prompts}
    assert "scm-login" in names
    assert "test_case_review" in names  # 既有 prompt 不受影响


async def test_scm_login_prompt_renders_guide(client: Client) -> None:
    result = await client.get_prompt("scm-login", {"session": "qa1"})
    text = result.messages[0].content.text
    assert "browser_connect" in text
    assert '会话 "qa1"' in text
    assert "login_with_captcha" in text
    assert "session_save_state" in text
    assert "http://127.0.0.1:9222" in text


async def test_scm_login_prompt_defaults(client: Client) -> None:
    result = await client.get_prompt("scm-login", {})
    text = result.messages[0].content.text
    assert '会话 "scm"' in text
    assert "https://demo18-scm.hoolinks.com/static/admin" in text

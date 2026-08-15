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
    # base_url 参数化：显式传入的地址必须进入指令文本（不依赖 .env 的 SCM_BASE_URL）
    result = await client.get_prompt(
        "scm-login", {"session": "scm", "base_url": "https://scm.example.com/static/admin"}
    )
    text = result.messages[0].content.text
    assert '会话 "scm"' in text
    assert "https://scm.example.com/static/admin" in text

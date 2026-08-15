from __future__ import annotations

import pytest
from fastmcp import Client
from fastmcp.client.transports import FastMCPTransport

from qa_automation.server import mcp


@pytest.fixture
async def client() -> Client[FastMCPTransport]:
    async with Client(transport=mcp) as mcp_client:
        yield mcp_client


async def test_list_tools(client: Client[FastMCPTransport]) -> None:
    tools = await client.list_tools()
    names = {tool.name for tool in tools}
    # 原示例工具仍在；浏览器控制工具集见 test_browser_tools.py
    assert "check_string_length" in names
    assert len(names) >= 15


async def test_check_string_length_ok(client: Client[FastMCPTransport]) -> None:
    result = await client.call_tool(
        "check_string_length", {"text": "hello", "max_length": 10}
    )
    assert result.data is not None
    assert "OK" in result.data


async def test_check_string_length_fail(client: Client[FastMCPTransport]) -> None:
    result = await client.call_tool(
        "check_string_length", {"text": "a" * 20, "max_length": 10}
    )
    assert result.data is not None
    assert "FAIL" in result.data

"""动作链测试：多步批处理 + 链尾统一观察 + antd nth 降级 + 部分失败收集。"""
from __future__ import annotations

import asyncio

import pytest
from fastmcp import Client

from qa_automation.server import mcp

_CHAIN_PAGE = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<button id="go" style="position:absolute;top:10px;left:10px;width:60px;height:30px;">GO</button>
<input id="i" style="position:absolute;top:50px;left:10px;width:120px;height:26px;">
<button id="ok" style="position:absolute;top:90px;left:10px;width:60px;height:30px;">确 定</button>
<div class="ant-message"><span></span></div>
<script>
  const msg = document.querySelector(".ant-message span");
  function show(t) {
    msg.innerHTML = '<div class="ant-message-notice"><div class="ant-message-custom-content"><span>' + t + '</span></div></div>';
  }
  document.getElementById("go").addEventListener("click", () => show("clicked"));
  document.getElementById("ok").addEventListener("click", () => show("submitted:" + document.getElementById("i").value));
</script></body></html>"""

_CHAIN_DROPDOWN_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<style>.ant-select-dropdown-hidden{display:none}</style>
</head><body>
<button id="go" style="position:absolute;top:10px;left:10px;width:60px;height:30px;">GO</button>
<div class="ant-message"><span></span></div>
<!-- 常驻 dropdown 层1（hidden）：nth=0 -->
<div class="ant-select-dropdown ant-select-dropdown-hidden" style="position:absolute;top:50px;left:10px;width:150px;">
  <ul class="ant-select-dropdown-menu" role="menu">
    <li title="固定改机" class="ant-select-dropdown-menu-item" role="menuitem">固定改机</li>
  </ul>
</div>
<!-- 常驻 dropdown 层2（可见激活层）：li[title=固定改机] 的 nth=1 -->
<div class="ant-select-dropdown" style="position:absolute;top:50px;left:10px;width:150px;z-index:10;">
  <ul class="ant-select-dropdown-menu" role="menu">
    <li title="固定改机" class="ant-select-dropdown-menu-item" role="menuitem">固定改机</li>
  </ul>
</div>
<script>
  const msg = document.querySelector(".ant-message span");
  function show(t) {
    msg.innerHTML = '<div class="ant-message-notice"><div class="ant-message-custom-content"><span>' + t + '</span></div></div>';
  }
  document.querySelectorAll(".ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-dropdown-menu-item").forEach(li => {
    li.addEventListener("click", () => {
      document.querySelectorAll(".ant-select-dropdown").forEach(d => d.classList.add("ant-select-dropdown-hidden"));
      show("已选择:" + li.textContent.trim());
    });
  });
  document.getElementById("go").addEventListener("click", () => show("clicked"));
</script></body></html>"""


@pytest.fixture
async def client() -> Client:
    async with Client(transport=mcp) as c:
        yield c


async def _setup(client: Client, page_html: str) -> None:
    await client.call_tool("browser_connect", {"mode": "launch", "headless": True})
    await client.call_tool("session_create", {"name": "ch", "use_default": False})
    await client.call_tool("page_goto", {"url": "data:text/html," + page_html, "session": "ch"})


async def test_chain_multi_step_unified_observation(client: Client) -> None:
    """多步链：click → fill → click，链尾统一观察一次（最后动作的消息）。"""
    await _setup(client, _CHAIN_PAGE)
    r = await client.call_tool(
        "execute_action_chain",
        {
            "session": "ch",
            "actions": [
                {"action": "click", "css": "#go"},
                {"action": "fill", "css": "#i", "value": "abc", "input_method": "type"},
                {"action": "click", "css": "#ok"},
            ],
        },
    )
    assert r.data["ok"] is True, r.data
    assert r.data["status"] == "success"
    assert r.data["executed"] == 3 and r.data["failed"] == []
    # 链尾观察捕获最后一步的消息（submit:abc 覆盖 clicked）
    assert r.data["observation"]["kind"] == "message"
    assert "submitted:abc" in r.data["observation"]["text"]
    await client.call_tool("browser_disconnect", {})


async def test_chain_antd_nth_fallback(client: Client) -> None:
    """antd 常驻 dropdown：主定位 nth=0 命中 hidden 层（预检跳过）→ fallback nth=1 命中可见层。"""
    await _setup(client, _CHAIN_DROPDOWN_PAGE)
    r = await client.call_tool(
        "execute_action_chain",
        {
            "session": "ch",
            "actions": [
                {"action": "click", "css": "#go"},  # 先确认基础动作
                {"action": "click", "css": 'li[title="固定改机"] >> nth=0'},  # hidden 层 → 自动降级 nth=1
            ],
        },
    )
    assert r.data["ok"] is True, r.data
    assert r.data["executed"] == 2
    # 链尾观察：visible 层选项被点击
    assert r.data["observation"]["kind"] == "message"
    assert "已选择:固定改机" in r.data["observation"]["text"]
    await client.call_tool("browser_disconnect", {})


async def test_chain_stop_on_error_false(client: Client) -> None:
    """stop_on_error=False：坏步收集失败继续，好步执行。"""
    await _setup(client, _CHAIN_PAGE)
    r = await client.call_tool(
        "execute_action_chain",
        {
            "session": "ch",
            "actions": [
                {"action": "click", "css": "#not-exist"},  # 失败
                {"action": "click", "css": "#go"},  # 成功
            ],
            "stop_on_error": False,
        },
    )
    assert r.data["ok"] is True, r.data
    assert r.data["status"] == "partial"
    assert r.data["executed"] == 1
    assert len(r.data["failed"]) == 1 and r.data["failed"][0]["action"] == "click"
    assert "not-exist" in r.data["failed"][0]["error"]
    assert r.data["observation"]["kind"] == "message"
    assert "clicked" in r.data["observation"]["text"]
    await client.call_tool("browser_disconnect", {})


async def test_chain_stop_on_error_true(client: Client) -> None:
    """stop_on_error=True：首步失败即抛错（含已完成步数）。"""
    await _setup(client, _CHAIN_PAGE)
    r = await client.call_tool(
        "execute_action_chain",
        {
            "session": "ch",
            "actions": [
                {"action": "click", "css": "#not-exist"},
                {"action": "click", "css": "#go"},
            ],
        },
    )
    assert r.data["ok"] is False
    assert "第 1 步" in r.data["error"] and "已完成 0 步" in r.data["error"]
    await client.call_tool("browser_disconnect", {})


async def test_chain_empty_actions(client: Client) -> None:
    """空动作列表报错。"""
    await _setup(client, _CHAIN_PAGE)
    r = await client.call_tool("execute_action_chain", {"session": "ch", "actions": []})
    assert r.data["ok"] is False and "不能为空" in r.data["error"]
    await client.call_tool("browser_disconnect", {})

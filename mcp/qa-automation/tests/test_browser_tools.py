from __future__ import annotations

import asyncio
import os
import re

import pytest
from fastmcp import Client

from qa_automation.server import mcp

BROWSER_TOOLS = {
    "browser_connect",
    "browser_status",
    "browser_disconnect",
    "session_create",
    "session_switch",
    "session_list",
    "session_close",
    "session_save_state",
    "page_goto",
    "page_title",
    "page_click",
    "page_fill",
    "page_screenshot",
}


@pytest.fixture
async def client() -> Client:
    async with Client(transport=mcp) as c:
        yield c


async def test_browser_tools_registered(client: Client) -> None:
    tools = await client.list_tools()
    names = {t.name for t in tools}
    assert BROWSER_TOOLS <= names
    # 新特性元数据：title/description/icons/tags 挂载（FastMCP 3.x @tool 参数）
    by_name = {t.name: t for t in tools}
    connect = by_name["browser_connect"]
    assert connect.title == "Browser: Connect"
    assert "接管" in (connect.description or "")


async def test_lifespan_injects_lifecycle(client: Client) -> None:
    # lifespan 上下文生效：browser_status 不抛错且如实报告未连接
    result = await client.call_tool("browser_status", {})
    assert result.data["ok"] is True
    assert result.data["connected"] is False


async def test_errors_when_not_connected(client: Client) -> None:
    r = await client.call_tool("session_create", {"name": "x"})
    assert r.data["ok"] is False
    assert "connect" in r.data["error"].lower()

    r2 = await client.call_tool("page_goto", {"url": "about:blank"})
    assert r2.data["ok"] is False
    assert "connect" in r2.data["error"].lower()


async def test_session_errors(client: Client) -> None:
    await client.call_tool("browser_connect", {"mode": "launch", "headless": True})
    try:
        r = await client.call_tool("session_create", {"name": "dup"})
        assert r.data["ok"] is True
        r2 = await client.call_tool("session_create", {"name": "dup"})
        assert r2.data["ok"] is False  # 重名
        r3 = await client.call_tool("session_switch", {"name": "nope"})
        assert r3.data["ok"] is False
    finally:
        await client.call_tool("browser_disconnect", {})


_FILL_PAGE = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<input id="i">
<div class="ant-message"><span></span></div>
<script>
  const i = document.getElementById("i");
  const m = document.querySelector(".ant-message span");
  let msgTimer = null;
  function show(t) {
    clearTimeout(msgTimer);
    m.innerHTML = '<div class="ant-message-notice"><div class="ant-message-custom-content"><span>' + t + '</span></div></div>';
    msgTimer = setTimeout(() => { m.innerHTML = ""; }, 3000);
  }
  i.addEventListener("input", () => { show("输入:" + i.value); });
  i.addEventListener("keydown", (e) => { if (e.key === "Enter") show("回车:" + i.value); });
</script></body></html>"""


async def test_fill_dual_modes_and_observation(client: Client, cdp_url: str) -> None:
    """fill/type 双输入模式 + press_enter + 输入后浮层/消息观察（副作用间接断言值写入）。"""
    await client.call_tool("browser_connect", {"mode": "attach", "cdp_url": cdp_url})
    await client.call_tool("session_create", {"name": "fill", "use_default": False})
    await client.call_tool("page_goto", {"url": "data:text/html," + _FILL_PAGE, "session": "fill"})

    # type 模式（逐字模拟打字）→ 输入事件触发 ant-message
    r = await client.call_tool(
        "page_interact",
        {"session": "fill", "action": "fill", "css": "#i", "value": "abc", "input_method": "type"},
    )
    assert r.data["ok"] is True, r.data
    assert r.data["observation"] is not None, "type 输入后应观察到消息"
    assert "输入:abc" in r.data["observation"]["text"]

    # page_fill 默认 type（打字机）+ press_enter → 回车事件触发消息（fill 后同样观察）
    r = await client.call_tool(
        "page_fill", {"session": "fill", "css": "#i", "value": "xyz", "press_enter": True}
    )
    assert r.data["ok"] is True, r.data
    assert r.data["press_enter"] is True and r.data["input_method"] == "type"

    # 显式 fill 模式（原生填充）
    r = await client.call_tool(
        "page_fill", {"session": "fill", "css": "#i", "value": "q2", "input_method": "fill"}
    )
    assert r.data["ok"] is True, r.data
    assert r.data["input_method"] == "fill"
    assert r.data["observation"] is not None
    assert "输入:q2" in r.data["observation"]["text"]

    # visualize=True：光标注入不崩，交互完成特效从 DOM 移除
    r = await client.call_tool(
        "page_interact",
        {"session": "fill", "action": "fill", "css": "#i", "value": "q", "visualize": True},
    )
    assert r.data["ok"] is True, r.data

    await client.call_tool("session_close", {"name": "fill"})
    await client.call_tool("browser_disconnect", {})


_ANTD_SELECT_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<style>.ant-select-dropdown-hidden{display:none}</style>
</head><body>
<div class="ant-select ant-select-sm" style="position:absolute;top:10px;left:10px;width:200px;height:32px;">
  <div class="ant-select-selection" role="combobox" aria-expanded="false">
    <input class="ant-select-search__field" style="opacity:0;position:absolute;">
    <span class="ant-select-selection__rendered">请选择</span>
  </div>
</div>
<div class="ant-message"><span></span></div>
<!-- portal 弹层：iframe/页面最底部，隐藏时 class 追加 ant-select-dropdown-hidden -->
<div style="position: absolute; top: 0px; left: 0px; width: 100%;"><div>
<div class="ant-select-dropdown ant-select-dropdown--single ant-select-dropdown-placement-bottomLeft ant-select-dropdown-hidden"
     id="drop" style="width: 200px; left: 24px; top: 50px; position: absolute; z-index: 10;">
  <div style="overflow: auto;"><ul class="ant-select-dropdown-menu ant-select-dropdown-menu-vertical ant-select-dropdown-menu-root" role="menu">
    <li unselectable="unselectable" class="ant-select-dropdown-menu-item" role="menuitem" aria-selected="false">切换改机</li>
    <li unselectable="unselectable" class="ant-select-dropdown-menu-item" role="menuitem" aria-selected="false">固定改机</li>
    <li unselectable="unselectable" class="ant-select-dropdown-menu-item" role="menuitem" aria-selected="false">含过敏原</li>
    <li unselectable="unselectable" class="ant-select-dropdown-menu-item" role="menuitem" aria-selected="false">不含过敏原</li>
  </ul></div>
</div>
</div></div>
<script>
  const select = document.querySelector(".ant-select");
  const drop = document.getElementById("drop");
  const rendered = document.querySelector(".ant-select-selection__rendered");
  const msg = document.querySelector(".ant-message span");
  let msgTimer = null;
  function show(t) {
    clearTimeout(msgTimer);
    msg.innerHTML = '<div class="ant-message-notice"><div class="ant-message-custom-content"><span>' + t + '</span></div></div>';
    msgTimer = setTimeout(() => { msg.innerHTML = ""; }, 3000);
  }
  select.addEventListener("click", () => {
    drop.classList.remove("ant-select-dropdown-hidden");
  });
  document.querySelectorAll(".ant-select-dropdown-menu-item").forEach(o => {
    o.addEventListener("click", () => {
      document.querySelectorAll(".ant-select-dropdown-menu-item").forEach(x => {
        x.classList.remove("ant-select-dropdown-menu-item-selected", "ant-select-dropdown-menu-item-active");
        x.setAttribute("aria-selected", "false");
      });
      o.classList.add("ant-select-dropdown-menu-item-selected", "ant-select-dropdown-menu-item-active");
      o.setAttribute("aria-selected", "true");
      drop.classList.add("ant-select-dropdown-hidden");
      rendered.textContent = o.textContent.trim();
      show("已选择:" + o.textContent.trim());
    });
  });
</script></body></html>"""


async def test_select_antd_dropdown(client: Client, cdp_url: str) -> None:
    """antd3 Select 一步选择（真实结构：portal 在底部、li role=menuitem、hidden 类）。"""
    await client.call_tool("browser_connect", {"mode": "attach", "cdp_url": cdp_url})
    await client.call_tool("session_create", {"name": "sel", "use_default": False})
    await client.call_tool("page_goto", {"url": "data:text/html," + _ANTD_SELECT_PAGE, "session": "sel"})

    # analyze：展开后聚焦 dropdown，选项按真实角色 menuitem 收集
    r = await client.call_tool(
        "page_interact", {"session": "sel", "action": "click", "role": "combobox"}
    )
    await asyncio.sleep(1)
    r = await client.call_tool("analyze_current_page", {"session": "sel"})
    assert r.data["focus_layer"]["kind"] == "dropdown", r.data
    opts = [e for e in r.data["elements"] if e["role"] == "menuitem"]
    assert {o["name"] for o in opts} >= {"切换改机", "固定改机", "含过敏原", "不含过敏原"}

    # 一步选择（menuitem 角色精确匹配）
    r = await client.call_tool(
        "page_interact",
        {"session": "sel", "action": "select", "role": "combobox", "value": "固定改机"},
    )
    assert r.data["ok"] is True, r.data
    assert r.data["observation"] is not None, "select 后应观察到消息"
    assert "已选择:固定改机" in r.data["observation"]["text"]

    # 歧义：'过敏原' 命中两个候选 → 报错并给出候选列表（不盲目点击）
    r = await client.call_tool(
        "page_interact",
        {"session": "sel", "action": "select", "role": "combobox", "value": "过敏原"},
    )
    assert r.data["ok"] is False
    assert "多个候选" in r.data["error"] and "不含过敏原" in r.data["error"]

    # 精确文本可消除歧义
    r = await client.call_tool(
        "page_interact",
        {"session": "sel", "action": "select", "role": "combobox", "value": "不含过敏原"},
    )
    assert r.data["ok"] is True, r.data
    assert "已选择:不含过敏原" in r.data["observation"]["text"]

    await client.call_tool("session_close", {"name": "sel"})
    await client.call_tool("browser_disconnect", {})


async def test_visualize_env_toggle(client: Client) -> None:
    """.env VISUAL_CURSOR_ENABLED 控制默认光标可视化（显式参数优先）。"""
    await client.call_tool("browser_connect", {"mode": "launch", "headless": True})
    await client.call_tool("session_create", {"name": "viz", "use_default": False})
    await client.call_tool("page_goto", {"url": "data:text/html,<h1>viz</h1>", "session": "viz"})

    # env=true → 未显式传 visualize 也启用
    os.environ["VISUAL_CURSOR_ENABLED"] = "true"
    r = await client.call_tool(
        "page_interact", {"session": "viz", "action": "click", "x": 100, "y": 100}
    )
    assert r.data["ok"] is True and r.data["visualize"] is True

    # env=false → 默认不显示
    os.environ["VISUAL_CURSOR_ENABLED"] = "false"
    r = await client.call_tool(
        "page_interact", {"session": "viz", "action": "click", "x": 120, "y": 120}
    )
    assert r.data["ok"] is True and r.data["visualize"] is False

    # 显式参数优先于 env
    r = await client.call_tool(
        "page_interact",
        {"session": "viz", "action": "click", "x": 140, "y": 140, "visualize": True},
    )
    assert r.data["ok"] is True and r.data["visualize"] is True
    del os.environ["VISUAL_CURSOR_ENABLED"]

    await client.call_tool("browser_disconnect", {})


async def test_attach_roundtrip(client: Client, cdp_url: str) -> None:
    """真实接管浏览器全链路：连接→会话→导航→断言→截图→清理。"""
    r = await client.call_tool("browser_connect", {"mode": "attach", "cdp_url": cdp_url})
    assert r.data["ok"] is True, r.data
    assert r.data["mode"] == "attach"
    r = await client.call_tool(
        "session_create", {"name": "smoke", "use_default": False}
    )
    assert r.data["ok"] is True

    r = await client.call_tool(
        "page_goto", {"url": "data:text/html,<h1>mcp-ok</h1><input id='i'>", "session": "smoke"}
    )
    assert r.data["ok"] is True

    r = await client.call_tool(
        "page_fill", {"css": "#i", "value": "qa", "session": "smoke"}
    )
    assert r.data["ok"] is True

    r = await client.call_tool("page_screenshot", {"session": "smoke"})
    assert r.data["ok"] is True
    assert r.data["path"].endswith(".png")

    r = await client.call_tool("session_close", {"name": "smoke"})
    assert r.data["ok"] is True

    r = await client.call_tool("browser_disconnect", {})
    assert r.data["ok"] is True

    r = await client.call_tool("browser_status", {})
    assert r.data["connected"] is False


async def test_session_open_isolated(client: Client, cdp_url: str) -> None:
    """隔离窗口工具：创建 cookie 隔离会话（无痕窗口）。"""
    r = await client.call_tool("browser_connect", {"mode": "attach", "cdp_url": cdp_url})
    assert r.data["ok"] is True
    r = await client.call_tool(
        "session_open_isolated", {"name": "iso", "fullscreen": False}
    )
    assert r.data["ok"] is True
    assert r.data["isolated"] is True
    assert r.data["logged_in"] is False
    # 会话确实为隔离上下文（default=False）
    r = await client.call_tool("session_list", {})
    iso = next(s for s in r.data["sessions"] if s["name"] == "iso")
    assert iso["default"] is False
    # 账号绑定映射（防串窗）：session_create 显式 account
    r = await client.call_tool(
        "session_create", {"name": "bounded", "use_default": False, "account": "admin"}
    )
    assert r.data["ok"] is True
    r = await client.call_tool("session_list", {})
    bounded = next(s for s in r.data["sessions"] if s["name"] == "bounded")
    assert bounded["account"] == "admin"
    await client.call_tool("session_close", {"name": "bounded"})
    await client.call_tool("session_close", {"name": "iso"})
    await client.call_tool("browser_disconnect", {})


async def test_browser_connect_uses_env_cdp_url(client: Client, monkeypatch) -> None:
    """CDP_URL 环境变量覆盖默认接管端口。"""
    monkeypatch.setenv("CDP_URL", "http://127.0.0.1:19999")
    r = await client.call_tool("browser_connect", {"mode": "attach"})
    assert r.data["ok"] is False  # 19999 无浏览器监听
    assert "19999" in r.data["error"]


async def test_tab_list_and_switch(client: Client, cdp_url: str) -> None:
    """标签页接管：list 找到业务页，switch 切换会话到该页（不新建）。"""
    r = await client.call_tool("browser_connect", {"mode": "attach", "cdp_url": cdp_url})
    assert r.data["ok"] is True
    await client.call_tool("session_create", {"name": "tabs"})
    r = await client.call_tool("tab_list", {"session": "tabs"})
    assert r.data["ok"] is True
    tabs = r.data["tabs"]
    assert len(tabs) >= 1
    # 切换到第一个标签页
    r = await client.call_tool("tab_switch", {"session": "tabs", "index": 0})
    assert r.data["ok"] is True
    # URL 子串匹配切换
    first_url = tabs[0]["url"]
    if first_url and len(first_url) > 5:
        sub = first_url[:10]
        r = await client.call_tool("tab_switch", {"session": "tabs", "url_contains": sub})
        assert r.data["ok"] is True
    await client.call_tool("browser_disconnect", {})

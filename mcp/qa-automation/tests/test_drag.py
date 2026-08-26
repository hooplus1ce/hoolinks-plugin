"""通用真实鼠标拖拽（page_drag、page_interact drag、action_chain drag、recorder drag）测试。"""
from __future__ import annotations

import urllib.parse

import pytest
from fastmcp import Client

from qa_automation.server import mcp

_DRAG_HTML = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    #box {
      position: absolute;
      top: 50px;
      left: 50px;
      width: 60px;
      height: 60px;
      background: red;
      cursor: pointer;
    }
    #target {
      position: absolute;
      top: 50px;
      left: 300px;
      width: 80px;
      height: 80px;
      background: blue;
    }
    #status {
      position: absolute;
      top: 200px;
      left: 50px;
    }
  </style>
</head>
<body>
  <div id="box" role="button" aria-label="滑块">滑块</div>
  <div id="target" role="region" aria-label="目标区">目标区</div>
  <div id="status">idle</div>
  <div class="ant-message"><span></span></div>

  <script>
    const box = document.getElementById("box");
    const status = document.getElementById("status");
    const msg = document.querySelector(".ant-message span");
    let isDragging = false;
    let startX = 0, startY = 0;

    box.addEventListener("mousedown", (e) => {
      isDragging = true;
      startX = e.clientX;
      startY = e.clientY;
      status.textContent = `dragstart:${startX},${startY}`;
    });

    window.addEventListener("mousemove", (e) => {
      if (isDragging) {
        status.textContent = `dragging:${e.clientX},${e.clientY}`;
      }
    });

    window.addEventListener("mouseup", (e) => {
      if (isDragging) {
        isDragging = false;
        status.textContent = `dragend:${e.clientX},${e.clientY}`;
        msg.innerHTML = '<div class="ant-message-notice"><div class="ant-message-custom-content"><span>' + status.textContent + '</span></div></div>';
      }
    });
  </script>
</body>
</html>"""


@pytest.fixture
async def client() -> Client:
    async with Client(transport=mcp) as c:
        yield c


async def _setup(client: Client, cdp_url: str, html: str = _DRAG_HTML) -> None:
    await client.call_tool("browser_connect", {"mode": "attach", "cdp_url": cdp_url})
    await client.call_tool("session_create", {"name": "drag_session", "use_default": False})
    data_url = "data:text/html;charset=utf-8," + urllib.parse.quote(html)
    await client.call_tool("page_goto", {"url": data_url, "session": "drag_session"})


async def test_page_drag_coordinate_mode(client: Client, cdp_url: str) -> None:
    """坐标模式通用拖拽：(from_x, from_y) -> (to_x, to_y)。"""
    await _setup(client, cdp_url)
    try:
        r = await client.call_tool(
            "page_drag",
            {
                "session": "drag_session",
                "from_x": 80,
                "from_y": 80,
                "to_x": 340,
                "to_y": 90,
                "steps": 10,
                "delay_ms": 50,
            },
        )
        assert r.data.get("ok") is True, f"page_drag coordinate failed: {r.data}"
        assert r.data["mode"] == "coordinate"
        assert r.data["action"] == "drag"
        assert r.data["from"]["x"] == 80.0
        assert r.data["from"]["y"] == 80.0
        assert r.data["to"]["x"] == 340.0
        assert r.data["to"]["y"] == 90.0
    finally:
        await client.call_tool("session_close", {"name": "drag_session"})


async def test_page_drag_locator_mode(client: Client, cdp_url: str) -> None:
    """语义定位模式通用拖拽：从元素 A 拖到元素 B。"""
    await _setup(client, cdp_url)
    try:
        r = await client.call_tool(
            "page_drag",
            {
                "session": "drag_session",
                "from_role": "button",
                "from_name": "滑块",
                "to_role": "region",
                "to_name": "目标区",
                "steps": 10,
                "delay_ms": 50,
            },
        )
        assert r.data.get("ok") is True, f"page_drag locator failed: {r.data}"
        assert r.data["mode"] == "locator"
        assert r.data["action"] == "drag"
        assert "from_locator" in r.data
        assert "to_locator" in r.data
        # 起始元素中心在 (80, 80)，目标元素中心在 (340, 90)
        assert abs(r.data["from"]["x"] - 80) < 5
        assert abs(r.data["from"]["y"] - 80) < 5
        assert abs(r.data["to"]["x"] - 340) < 5
        assert abs(r.data["to"]["y"] - 90) < 5
    finally:
        await client.call_tool("session_close", {"name": "drag_session"})


async def test_page_interact_drag(client: Client, cdp_url: str) -> None:
    """page_interact 动作支持 action='drag'。"""
    await _setup(client, cdp_url)
    try:
        # 1. 坐标模式
        r1 = await client.call_tool(
            "page_interact",
            {
                "session": "drag_session",
                "action": "drag",
                "x": 80,
                "y": 80,
                "to_x": 200,
                "to_y": 80,
            },
        )
        assert r1.data.get("ok") is True, f"page_interact coordinate failed: {r1.data}"
        assert r1.data["action"] == "drag"
        assert r1.data["to_x"] == 200

        # 2. 定位模式
        r2 = await client.call_tool(
            "page_interact",
            {
                "session": "drag_session",
                "action": "drag",
                "css": "#box",
                "to_x": 340,
                "to_y": 90,
            },
        )
        assert r2.data.get("ok") is True, f"page_interact locator failed: {r2.data}"
        assert r2.data["action"] == "drag"
    finally:
        await client.call_tool("session_close", {"name": "drag_session"})


async def test_action_chain_drag(client: Client, cdp_url: str) -> None:
    """execute_action_chain 批量动作链支持 drag 动作步骤。"""
    await _setup(client, cdp_url)
    try:
        r = await client.call_tool(
            "execute_action_chain",
            {
                "session": "drag_session",
                "actions": [
                    {"action": "drag", "x": 80, "y": 80, "to_x": 340, "to_y": 90, "steps": 5, "delay_ms": 20},
                ],
            },
        )
        assert r.data.get("ok") is True, f"action_chain failed: {r.data}"
        assert r.data["executed"] == 1
        assert "dragend:340,90" in (r.data["observation"]["text"] or "")
    finally:
        await client.call_tool("session_close", {"name": "drag_session"})


async def test_page_drag_missing_args(client: Client, cdp_url: str) -> None:
    """缺少必要参数时返回明确错误。"""
    await _setup(client, cdp_url)
    try:
        r = await client.call_tool(
            "page_drag",
            {
                "session": "drag_session",
                "from_x": 80,
                # 缺少 from_y 与终点
            },
        )
        assert r.data["ok"] is False
        assert "drag requires" in r.data["error"]
    finally:
        await client.call_tool("session_close", {"name": "drag_session"})

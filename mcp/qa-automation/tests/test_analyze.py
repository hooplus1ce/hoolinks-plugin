"""analyze_current_page 测试：mock 激活功能页（面包屑/标签/iframe），验证收集与坐标偏移。"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastmcp import Client

from qa_automation.server import mcp

_MOCK_PAGE = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<div class="ant-breadcrumb" style="margin-left: 22px;">
  <span><span class="ant-breadcrumb-link"><span>产品工艺</span></span><span class="ant-breadcrumb-separator">&gt;</span></span>
  <span><span class="ant-breadcrumb-link"><span>清洗改机设置</span></span></span>
</div>
<div class="ant-tabs-nav">
  <div class="ant-tabs-nav-scroll"><div class="ant-tabs-nav">
    <div role="tab" class=" ant-tabs-tab"><div><span>采购订单</span></div></div>
    <div role="tab" class="ant-tabs-tab-active ant-tabs-tab"><div><span>清洗改机设置</span></div></div>
  </div></div>
</div>
<div role="tabpanel" aria-hidden="false" class="ant-tabs-tabpane ant-tabs-tabpane-active">
  <iframe id="react_iframe" name="66250001" style="position: absolute; top: 120px; left: 50px; width: 800px; height: 600px;"
    srcdoc="<!doctype html><html><body style='margin:0'><button id='btnAdd' style='position:absolute;top:10px;left:10px;width:70px;height:30px'>新 增</button><input placeholder='请输入单号' style='position:absolute;top:60px;left:10px;width:180px;height:28px'><select style='position:absolute;top:110px;left:10px;width:120px;height:28px'><option>全部</option></select></body></html>"></iframe>
</div>
</body></html>"""


class _MockPage(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = _MOCK_PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


@pytest.fixture
def mock_page() -> str:
    server = HTTPServer(("127.0.0.1", 0), _MockPageGeneric)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


@pytest.fixture
async def client() -> Client:
    async with Client(transport=mcp) as c:
        yield c


async def test_analyze_current_page(client: Client, mock_page: str) -> None:
    r = await client.call_tool("browser_connect", {"mode": "launch", "headless": True})
    assert r.data["ok"] is True
    await client.call_tool("session_create", {"name": "an", "use_default": False})
    r = await client.call_tool("page_goto", {"url": mock_page, "session": "an"})
    assert r.data["ok"] is True

    r = await client.call_tool("analyze_current_page", {"session": "an"})
    assert r.data["ok"] is True, r.data
    data = r.data

    # 面包屑路径
    assert data["breadcrumb"] == ["产品工艺", "清洗改机设置"]

    # 标签栏 + 激活标记
    titles = [t["title"] for t in data["tabs"]]
    assert "采购订单" in titles and "清洗改机设置" in titles
    active = [t for t in data["tabs"] if t["active"]]
    assert active and active[0]["title"] == "清洗改机设置"

    # 激活 iframe
    iframe = data["active_iframe"]
    assert iframe is not None
    assert iframe["id"] == "react_iframe"
    assert iframe["name"] == "66250001"

    # 可交互元素
    by_role = {e["role"] for e in data["elements"]}
    assert "button" in by_role
    assert "textbox" in by_role
    assert "combobox" in by_role
    add_btn = next(e for e in data["elements"] if e["name"] == "新 增")
    # 定位信息齐全（语义定位首选 + 视口绝对坐标）
    assert add_btn["css"] and add_btn["xpath"] and add_btn["gbr"]["role"] == "button"
    # ref 全局编号 + input_type
    assert add_btn["ref"].startswith("e")
    # 视口绝对坐标（元素中心）= iframe 偏移(50,120) + iframe 内中心(45,25)
    assert add_btn["x"] == 95.0
    assert add_btn["y"] == 145.0
    tb = next(e for e in data["elements"] if e["role"] == "textbox")
    assert tb["input_type"] == "text"

    await client.call_tool("browser_disconnect", {})


async def test_semantic_interaction(client: Client, mock_page: str) -> None:
    """语义定位交互：get_by_role 点击 + get_by_placeholder 填充（iframe 内）。"""
    r = await client.call_tool("browser_connect", {"mode": "launch", "headless": True})
    assert r.data["ok"] is True
    await client.call_tool("session_create", {"name": "sem", "use_default": False})
    await client.call_tool("page_goto", {"url": mock_page, "session": "sem"})

    # iframe 内按钮语义点击（真实场景：按钮文本带空格"新 增"，name 取自 analyze）
    r = await client.call_tool(
        "page_click", {"session": "sem", "role": "button", "name": "新 增"}
    )
    assert r.data["ok"] is True, r.data
    # iframe 内输入框按 placeholder 填充
    r = await client.call_tool(
        "page_fill", {"session": "sem", "placeholder": "请输入单号", "value": "PO-001"}
    )
    assert r.data["ok"] is True, r.data
    # 语义定位参数校验（多维度互斥）
    r = await client.call_tool(
        "page_click", {"session": "sem", "role": "button", "css": "#btnAdd"}
    )
    assert r.data["ok"] is False  # 多定位维度应报错
    await client.call_tool("browser_disconnect", {})


_MOCK_PAGE_TOP = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<button id="topBtn" style="position:absolute;top:10px;left:10px;width:80px;height:30px">顶层按钮</button>
<div role="tabpanel" aria-hidden="false" class="ant-tabs-tabpane ant-tabs-tabpane-active">
  <iframe id="f2" name="f2n" style="position:absolute;top:200px;left:100px;width:600px;height:400px;"
    srcdoc="<html><body><button id='inBtn' style='position:absolute;top:20px;left:20px;width:70px;height:30px'>iframe按钮</button></body></html>"></iframe>
</div>
</body></html>"""

_MOCK_PAGE_MODAL = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<button id="bgBtn" style="position:absolute;top:10px;left:10px;width:80px;height:30px">背景按钮</button>
<div class="ant-modal-wrap">
  <div class="ant-modal" style="position:absolute;left:200px;top:100px;width:400px;height:200px;">
    <div class="ant-modal-title">新增清洗改机规则</div>
    <div class="ant-modal-body">
      <input id="ruleName" placeholder="规则名称" style="width:150px;height:28px;">
      <button id="confirmBtn" style="width:60px;height:30px;">确 定</button>
    </div>
  </div>
</div>
</body></html>"""


class _MockPageGeneric(BaseHTTPRequestHandler):
    """按 path 返回不同 mock 页面。"""

    def do_GET(self) -> None:
        if "/top" in self.path:
            body = _MOCK_PAGE_TOP.encode()
        elif "/modal" in self.path:
            body = _MOCK_PAGE_MODAL.encode()
        else:
            body = _MOCK_PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


async def _setup(client, mock_page: str, path: str) -> str:
    await client.call_tool("browser_connect", {"mode": "launch", "headless": True})
    await client.call_tool("session_create", {"name": "an", "use_default": False})
    await client.call_tool("page_goto", {"url": mock_page + path, "session": "an"})
    return "an"


async def test_analyze_top_level_and_iframe(client: Client, mock_page: str) -> None:
    """顶层 DOM 与激活 iframe 元素同时收集（frame 标记区分）。"""
    sess = await _setup(client, mock_page, "/top")
    r = await client.call_tool("analyze_current_page", {"session": sess})
    assert r.data["ok"] is True, r.data
    top_btn = next((e for e in r.data["elements"] if e["name"] == "顶层按钮"), None)
    iframe_btn = next((e for e in r.data["elements"] if e["name"] == "iframe按钮"), None)
    assert top_btn is not None and top_btn["frame"] == "top"
    assert iframe_btn is not None and iframe_btn["frame"] == "iframe"
    # iframe 坐标叠加偏移（iframe 在 100,200；按钮 20,20 尺寸 70x30 → 中心 55,35 → 视口 155,235）
    assert iframe_btn["x"] == 155.0 and iframe_btn["y"] == 235.0
    await client.call_tool("browser_disconnect", {})


async def test_analyze_modal_focus_trim(client: Client, mock_page: str) -> None:
    """存在可见 modal 时：聚焦弹层，只输出弹层内元素（裁剪背景）。"""
    sess = await _setup(client, mock_page, "/modal")
    r = await client.call_tool("analyze_current_page", {"session": sess})
    assert r.data["ok"] is True, r.data
    assert r.data["focus_layer"]["kind"] == "modal"
    names = {e["name"] for e in r.data["elements"]}
    # 弹层内元素
    assert "确 定" in names
    # 背景元素被裁剪（聚焦弹层）
    assert "背景按钮" not in names
    assert all(e["frame"] == "layer" for e in r.data["elements"])
    await client.call_tool("browser_disconnect", {})


async def test_page_interact_modes(client: Client, mock_page: str) -> None:
    """page_interact 通用交互：语义（空格名）/坐标（不计算直接用）/xpath/in_iframe 开关。"""
    r = await client.call_tool("browser_connect", {"mode": "launch", "headless": True})
    assert r.data["ok"] is True
    await client.call_tool("session_create", {"name": "pi", "use_default": False})
    await client.call_tool("page_goto", {"url": mock_page, "session": "pi"})

    # 1) 语义定位（iframe 内按钮，name 带空格）
    r = await client.call_tool(
        "page_interact",
        {"session": "pi", "action": "click", "role": "button", "name": "新 增"},
    )
    assert r.data["ok"] is True, r.data
    assert r.data["mode"] == "locator"

    # 2) 坐标模式：视口坐标直接使用（iframe 偏移 50,120 + 按钮内部 10,10）
    r = await client.call_tool(
        "page_interact", {"session": "pi", "action": "click", "x": 60, "y": 130}
    )
    assert r.data["ok"] is True, r.data
    assert r.data["mode"] == "coordinate"

    # 3) xpath 模式（用 analyze 返回的 xpath）
    r = await client.call_tool("analyze_current_page", {"session": "pi"})
    btn = next(e for e in r.data["elements"] if e["name"] == "新 增")
    r = await client.call_tool(
        "page_interact", {"session": "pi", "action": "click", "xpath": btn["xpath"]}
    )
    assert r.data["ok"] is True, r.data

    # 4) in_iframe=False：iframe 内元素顶层找不到（快速失败）
    r = await client.call_tool(
        "page_interact",
        {"session": "pi", "action": "click", "role": "button", "name": "新 增", "in_iframe": False, "timeout_ms": 3000},
    )
    assert r.data["ok"] is False

    # 5) 坐标缺参报错
    r = await client.call_tool("page_interact", {"session": "pi", "action": "click", "x": 60})
    assert r.data["ok"] is False
    await client.call_tool("browser_disconnect", {})

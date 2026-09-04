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


@pytest.fixture(scope="session")
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


async def test_analyze_current_page(client: Client, cdp_url: str, mock_page: str) -> None:
    sess = await _setup(client, cdp_url, mock_page, "", "an")
    try:
        r = await client.call_tool("analyze_current_page", {"session": sess})
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
        # 定位信息齐全（语义定位首选 + 视口绝对坐标；xpath 不输出以降 token）
        assert add_btn["css"] and add_btn["gbr"]["role"] == "button"
        # ref 全局编号 + input_type
        assert add_btn["ref"].startswith("e")
        # 视口绝对坐标（元素中心）= iframe 偏移(50,120) + iframe 内中心(45,25)
        assert add_btn["x"] == 95.0
        assert add_btn["y"] == 145.0
        tb = next(e for e in data["elements"] if e["role"] == "textbox")
        assert tb["input_type"] == "text"
    finally:
        await client.call_tool("session_close", {"name": sess})


async def test_semantic_interaction(client: Client, cdp_url: str, mock_page: str) -> None:
    """语义定位交互：get_by_role 点击 + get_by_placeholder 填充（iframe 内）。"""
    sess = await _setup(client, cdp_url, mock_page, "", "sem")
    try:
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
        # 无任何有效定位维度时应报错
        r_err = await client.call_tool("page_click", {"session": "sem"})
        assert r_err.data["ok"] is False
    finally:
        await client.call_tool("session_close", {"name": "sem"})

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


_MOCK_PAGE_VTABLE = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<button id="bgBtn2" style="position:absolute;top:10px;left:10px;width:80px;height:30px">背景按钮2</button>
<div class="vtable" style="position:absolute;top:100px;left:50px;width:600px;height:300px;">
  <canvas id="vtCanvas" style="position:absolute;top:0;left:0;width:600px;height:300px;"></canvas>
  <div class="vtable__menu-element vtable__menu-element--hidden" style="position:absolute;"></div>
</div>
<div class="vtable-filter-menu" style="position:absolute;left:278px;top:267px;width:300px;height:315px;">
  <div><button style="width:80px;height:30px;">按值筛选</button><button style="width:80px;height:30px;">按条件筛选</button></div>
  <div><input placeholder="可使用空格分隔多个关键词" style="width:200px;height:28px;"></div>
  <div>
    <div><label><input type="checkbox" style="width:14px;height:14px;">全选</label></div>
    <div><label><input type="checkbox" style="width:14px;height:14px;">已审批</label></div>
  </div>
  <div><a href="#">清除筛选</a><button style="width:60px;height:30px;">取消</button><button style="width:60px;height:30px;">确认</button></div>
</div>
</body></html>"""


_MOCK_PAGE_VTABLE_EDITOR = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<button id="bgBtn3" style="position:absolute;top:10px;left:10px;width:80px;height:30px">背景按钮3</button>
<div tabindex="0" class="vtable" style="outline:none;margin:0;position:absolute;top:100px;left:50px;width:600px;height:200px;">
  <div data-vtable="vtable" class="input-container" style="opacity:0;pointer-events:none;"><input class="table-focus-control" readonly></div>
  <canvas id="vtCanvas" style="position:absolute;top:0;left:0;width:600px;height:200px;"></canvas>
  <div class="vtable__bubble-tooltip-element vtable__bubble-tooltip-element--hidden" style="left:143px;top:-4px;"><span>可编辑</span></div>
  <input type="hidden" value="">
  <input type="text" autocomplete="off" spellcheck="false" style="position:absolute;padding:4px 38px 4px 8px;width:203px;box-sizing:border-box;border:2px solid rgb(74,144,226);font-size:12px;top:21px;left:195px;height:26px;">
  <div style="position:absolute;display:flex;align-items:center;justify-content:center;width:30px;z-index:1;pointer-events:auto;cursor:pointer;top:21px;left:368px;height:26px;"><i class="anticon anticon-search" style="cursor:pointer;color:rgb(16,142,233);"></i></div>
  <div style="position:fixed;display:block;z-index:1000;background-color:rgb(255,255,255);border:1px solid rgb(217,217,217);border-radius:2px;box-shadow:rgba(0,0,0,0.15) 0px 2px 8px;overflow:hidden;top:342px;left:227px;width:203px;">
    <div style="position:relative;width:100%;overflow:hidden auto;height:250px;">
      <div class="virtual-option" style="padding:6px 12px;font-size:12px;cursor:pointer;position:absolute;top:0px;height:32px;box-sizing:border-box;">JK26A_152809</div>
      <div class="virtual-option" style="padding:6px 12px;font-size:12px;cursor:pointer;position:absolute;top:32px;height:32px;box-sizing:border-box;">JK45B_152731</div>
      <div class="virtual-option" style="padding:6px 12px;font-size:12px;cursor:pointer;position:absolute;top:64px;height:32px;box-sizing:border-box;">GT1_152729</div>
    </div>
  </div>
</div>
</body></html>"""

_MOCK_PAGE_RADIO_BUTTON = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<div class="page-title">
  <div class="ant-radio-group">
    <label class="ant-radio-button-wrapper ant-radio-button-wrapper-checked" style="display:inline-block;width:60px;height:32px;">
      <span class="ant-radio-button ant-radio-button-checked">
        <input type="radio" class="ant-radio-button-input" checked="" style="opacity:0;position:absolute;width:0;height:0;">
        <span class="ant-radio-button-inner"></span>
      </span>
      <span>汇总</span>
    </label>
    <label class="ant-radio-button-wrapper" style="display:inline-block;width:60px;height:32px;">
      <span class="ant-radio-button">
        <input type="radio" class="ant-radio-button-input" style="opacity:0;position:absolute;width:0;height:0;">
        <span class="ant-radio-button-inner"></span>
      </span>
      <span>明细</span>
    </label>
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
        elif "/radio" in self.path:
            body = _MOCK_PAGE_RADIO_BUTTON.encode()
        elif "/vtable-editor" in self.path:
            body = _MOCK_PAGE_VTABLE_EDITOR.encode()
        elif "/vtable" in self.path:
            body = _MOCK_PAGE_VTABLE.encode()
        else:
            body = _MOCK_PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass

async def _setup(client: Client, cdp_url: str, mock_page: str, path: str, name: str = "an") -> str:
    await client.call_tool("browser_connect", {"mode": "attach", "cdp_url": cdp_url})
    await client.call_tool("session_create", {"name": name, "use_default": False})
    await client.call_tool("page_goto", {"url": mock_page + path, "session": name})
    return name


async def test_analyze_top_level_and_iframe(client: Client, cdp_url: str, mock_page: str) -> None:
    """顶层 DOM 与激活 iframe 元素同时收集（frame 标记区分）。"""
    sess = await _setup(client, cdp_url, mock_page, "/top", "an_top")
    try:
        r = await client.call_tool("analyze_current_page", {"session": sess})
        assert r.data["ok"] is True, r.data
        top_btn = next((e for e in r.data["elements"] if e["name"] == "顶层按钮"), None)
        iframe_btn = next((e for e in r.data["elements"] if e["name"] == "iframe按钮"), None)
        assert top_btn is not None and top_btn["frame"] == "top"
        assert iframe_btn is not None and iframe_btn["frame"] == "iframe"
        # iframe 坐标叠加偏移（iframe 在 100,200；按钮 20,20 尺寸 70x30 → 中心 55,35 → 视口 155,235）
        assert iframe_btn["x"] == 155.0 and iframe_btn["y"] == 235.0
    finally:
        await client.call_tool("session_close", {"name": sess})


async def test_analyze_modal_focus_trim(client: Client, cdp_url: str, mock_page: str) -> None:
    """存在可见 modal 时：聚焦弹层，只输出弹层内元素（裁剪背景）。"""
    sess = await _setup(client, cdp_url, mock_page, "/modal", "an_modal")
    try:
        r = await client.call_tool("analyze_current_page", {"session": sess})
        assert r.data["ok"] is True, r.data
        assert r.data["focus_layer"]["kind"] == "modal"
        names = {e["name"] for e in r.data["elements"]}
        # 弹层内元素
        assert "确 定" in names
        # 背景元素被裁剪（聚焦弹层）
        assert "背景按钮" not in names
        assert all(e["frame"] == "layer" for e in r.data["elements"])
    finally:
        await client.call_tool("session_close", {"name": sess})


async def test_analyze_vtable_focus_trim(client: Client, cdp_url: str, mock_page: str) -> None:
    """存在可见 VTable 自绘弹层（.vtable-filter-menu）时：聚焦弹层，裁剪背景。"""
    sess = await _setup(client, cdp_url, mock_page, "/vtable", "an_vt")
    try:
        r = await client.call_tool("analyze_current_page", {"session": sess})
        assert r.data["ok"] is True, r.data
        assert r.data["focus_layer"]["kind"] == "dropdown"
        assert "vtable-filter-menu" in r.data["focus_layer"]["container"]
        names = {e["name"] for e in r.data["elements"]}
        # 弹层内元素（checkbox 由 label 包裹）
        assert "确认" in names
        assert "按值筛选" in names
        # 背景元素被裁剪（聚焦弹层）—— canvas 兄弟菜单 hidden 不误报
        assert "背景按钮2" not in names
        assert all(e["frame"] == "layer" for e in r.data["elements"])
    finally:
        await client.call_tool("session_close", {"name": sess})


async def test_detect_overlays(client: Client, cdp_url: str, mock_page: str) -> None:
    """detect_overlays：可见弹层 + 隐藏态常驻容器分类（modal/dropdown/vtable-filter）。"""
    sess = await _setup(client, cdp_url, mock_page, "/vtable", "an_ov")
    try:
        # 默认 include_hidden=False：只返回可见弹层
        r = await client.call_tool("detect_overlays", {"session": sess})
        assert r.data["ok"] is True, r.data
        assert r.data["visible_count"] == 1
        vl = r.data["visible_layers"][0]
        assert vl["kind"] == "vtable-filter"
        assert "vtable-filter-menu" in vl["container"]
        assert vl["visible"] is True
        # mock /vtable 页面弹层在顶层 body（无 iframe 包裹）→ scope=top
        assert vl["scope"] == "top"
        # 焦点弹层
        assert r.data["focus_layer"]["kind"] == "vtable-filter"

        # include_hidden=True：列出隐藏态常驻容器（modal/dropdown 等）
        r2 = await client.call_tool(
            "detect_overlays", {"session": sess, "include_hidden": True}
        )
        assert r2.data["ok"] is True, r2.data
        kinds = {l["kind"] for l in r2.data["hidden_layers"]}
        # mock 页面无 antd modal/select-dropdown，但 VTable 隐藏菜单应列出
        assert any("vtable" in k for k in kinds)
        # 可见层仍被正确标记
        assert all(l["visible"] is False for l in r2.data["hidden_layers"])
    finally:
        await client.call_tool("session_close", {"name": sess})


async def test_page_interact_modes(client: Client, cdp_url: str, mock_page: str) -> None:
    """page_interact 通用交互：语义（空格名）/坐标（不计算直接用）/xpath/in_iframe 开关。"""
    sess = await _setup(client, cdp_url, mock_page, "", "pi")
    try:
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

        # 3) xpath 模式（XPath 表达式定位 iframe 内按钮；analyze 不再输出 xpath）
        r = await client.call_tool(
            "page_interact", {"session": "pi", "action": "click", "xpath": "/html/body/button"}
        )
        assert r.data["ok"] is True, r.data

        # 4) in_iframe=False：iframe 内元素顶层找不到（快速失败）
        r = await client.call_tool(
            "page_interact",
            {"session": "pi", "action": "click", "role": "button", "name": "新 增", "in_iframe": False, "timeout_ms": 300},
        )
        assert r.data["ok"] is False

        # 5) 坐标缺参报错
        r = await client.call_tool("page_interact", {"session": "pi", "action": "click", "x": 60})
        assert r.data["ok"] is False
    finally:
        await client.call_tool("session_close", {"name": "pi"})


async def test_detect_overlays_editor_dropdown(client: Client, cdp_url: str, mock_page: str) -> None:
    """VTable 单元格编辑器下拉（无 class 面板 + .virtual-option 选项）被识别为可见弹层。"""
    sess = await _setup(client, cdp_url, mock_page, "/vtable-editor", "an_ed")
    try:
        r = await client.call_tool("detect_overlays", {"session": sess})
        assert r.data["ok"] is True, r.data
        kinds = {l["kind"] for l in r.data["visible_layers"]}
        assert "vtable-editor-dropdown" in kinds
        layer = next(
            l for l in r.data["visible_layers"] if l["kind"] == "vtable-editor-dropdown"
        )
        assert layer["visible"] is True
        assert layer["scope"] == "top"
        # 选项文本进入摘要（可见选项可据此定位）
        assert "JK26A_152809" in layer["text"]
        assert "GT1_152729" in layer["text"]
        # 焦点弹层优先指向编辑器下拉
        assert r.data["focus_layer"]["kind"] == "vtable-editor-dropdown"
    finally:
        await client.call_tool("session_close", {"name": sess})


async def test_analyze_vtable_editor_focus(client: Client, cdp_url: str, mock_page: str) -> None:
    """VTable 单元格编辑器下拉打开时：聚焦弹层，选项以 role=option 暴露（背景裁剪）。"""
    sess = await _setup(client, cdp_url, mock_page, "/vtable-editor", "an_vf")
    try:
        r = await client.call_tool("analyze_current_page", {"session": sess})
        assert r.data["ok"] is True, r.data
        assert r.data["focus_layer"]["kind"] == "dropdown"
        # 无 class 面板 → 结构选择器 + nth 消歧（第 4 个 DIV 子节点 = 编辑器下拉面板）
        assert r.data["focus_layer"]["container"] == "div.vtable > div >> nth=3"
        names = {e["name"] for e in r.data["elements"]}
        assert "JK26A_152809" in names
        assert "JK45B_152731" in names
        assert "GT1_152729" in names
        opts = [e for e in r.data["elements"] if e["role"] == "option"]
        assert len(opts) == 3
        # 背景元素被裁剪（聚焦弹层），全部来自弹层
        assert "背景按钮3" not in names
        assert all(e["frame"] == "layer" for e in r.data["elements"])
        # 视口坐标：fixed 面板 top=342 + 1px 边框 + 选项绝对偏移 + 高度一半
        first = next(e for e in opts if e["name"] == "JK26A_152809")
        assert first["y"] == 359.0
        assert first["css"]
    finally:
        await client.call_tool("session_close", {"name": sess})


async def test_page_interact_virtual_option(client: Client, cdp_url: str, mock_page: str) -> None:
    """单元格编辑器下拉选项可按 analyze 的 role=option + name 点击（无显式 role 兜底）。"""
    sess = await _setup(client, cdp_url, mock_page, "/vtable-editor", "an_vo")
    try:
        r = await client.call_tool(
            "page_interact",
            {"session": sess, "action": "click", "role": "option", "name": "JK45B_152731"},
        )
        assert r.data["ok"] is True, r.data
        assert r.data["mode"] == "locator"
    finally:
        await client.call_tool("session_close", {"name": sess})


async def test_page_click_observes_editor_dropdown(client: Client, cdp_url: str, mock_page: str) -> None:
    """page_click 点击后快速观察：编辑器下拉弹层进入 observation（零等待返回）。"""
    sess = await _setup(client, cdp_url, mock_page, "/vtable-editor", "an_od")
    try:
        r = await client.call_tool(
            "page_click", {"session": sess, "role": "option", "name": "JK45B_152731"}
        )
        assert r.data["ok"] is True, r.data
        obs = r.data.get("observation")
        assert obs is not None
        assert obs["layer"]["kind"] == "dropdown"
        assert obs["url_changed"] is False
        assert obs["iframe"] is None  # mock 页面无激活 iframe
    finally:
        await client.call_tool("session_close", {"name": sess})


async def test_page_click_observes_navigation(client: Client, cdp_url: str, mock_page: str) -> None:
    """page_click 点击链接后观察：URL 跳转被标记 url_changed=True。"""
    sess = await _setup(client, cdp_url, mock_page, "/vtable", "an_nav")
    try:
        r = await client.call_tool(
            "page_click", {"session": sess, "css": "a:has-text('清除筛选')"}
        )
        assert r.data["ok"] is True, r.data
        obs = r.data.get("observation")
        assert obs is not None
        assert obs["url_changed"] is True
        assert obs["url"].endswith("#")
    finally:
        await client.call_tool("session_close", {"name": sess})


async def test_page_click_observes_iframe(client: Client, cdp_url: str, mock_page: str) -> None:
    """page_click 点击 iframe 内元素后观察：激活 iframe 信息（id/name）进入 observation。"""
    sess = await _setup(client, cdp_url, mock_page, "/", "an_ifr")
    try:
        r = await client.call_tool(
            "page_click", {"session": sess, "role": "button", "name": "新 增"}
        )
        assert r.data["ok"] is True, r.data
        obs = r.data.get("observation")
        assert obs is not None
        assert obs["iframe"] is not None
        assert obs["iframe"]["name"] == "66250001"
        assert obs["iframe"]["id"] == "react_iframe"
        assert obs["url_changed"] is False
        assert obs["iframe_changed"] is False
    finally:
        await client.call_tool("session_close", {"name": sess})

async def test_analyze_radio_button_group(client: Client, cdp_url: str, mock_page: str) -> None:
    """泛化识别各类单选/按钮组（.ant-radio-button-wrapper，内部 input 为 opacity:0 隐藏）。"""
    sess = await _setup(client, cdp_url, mock_page, "/radio", "an_radio")
    try:
        r = await client.call_tool("analyze_current_page", {"session": sess})
        assert r.data["ok"] is True, r.data
        radios = [e for e in r.data["elements"] if e["role"] == "radio"]
        assert len(radios) == 2
        names = {e["name"] for e in radios}
        assert "汇总" in names and "明细" in names
        first = next(e for e in radios if e["name"] == "汇总")
        assert first["role"] == "radio"
        # 并且可以直接使用语义定位进行点击操作
        r_click = await client.call_tool(
            "page_click", {"session": sess, "role": "radio", "name": "明细"}
        )
        assert r_click.data["ok"] is True
    finally:
        await client.call_tool("session_close", {"name": sess})

pytestmark = pytest.mark.slow

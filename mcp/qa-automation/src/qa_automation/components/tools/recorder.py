"""测试用例录制与导出 MCP 工具集。

录制链路：start_recording 开启会话 → execute_and_record 逐条执行动作并记录
"最佳解析定位器"（语义优先：role > text > placeholder > xpath > css，坐标模式兜底）
→ export_session 一键落盘 JSON 资产 + Shadcn 风格 Excel 用例。

录制会话数据经 FastMCP 官方会话态（Context.set_state/get_state）存取，
按 MCP session 隔离，取代进程级全局变量——HTTP/多会话部署下互不串扰。
"""
from __future__ import annotations

import json

from fastmcp import Context
from fastmcp.tools import tool
from mcp.types import Icon
from pydantic import BaseModel, Field

from qa_automation.browser.excel_render import render_shadcn_excel
from qa_automation.components.tools.browser import (
    _antd_select_option,
    _do_fill_with_visual,
    _err,
    _lifecycle,
    _resolve_locator,
)
from qa_automation.config import ELEMENT_WAIT_TIMEOUT_MS, OUTPUT_DIR

# 录制会话状态键：经 FastMCP 官方会话态 (Context.set_state/get_state) 存取。
SESSION_KEY = "recording_session"

_RECORD_ICON = Icon(
    src="data:image/svg+xml;base64,PHN2ZyB4bWxucz0naHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmcnIHdpZHRoPScyNCcgaGVpZ2h0PScyNCc+PGNpcmNsZSBjeD0nMTInIGN5PScxMicgcj0nMTAnIGZpbGw9J25vbmUnIHN0cm9rZT0nJTIzZGMyNjI2JyBzdHJva2Utd2lkdGg9JzInLz48Y2lyY2xlIGN4PScxMicgY3k9JzEyJyByPSc1JyBmaWxsPSclMjNkYzI2MjYnLz48L3N2Zz4=",
    mime_type="image/svg+xml",
)

# 计算 iframe 链式路径所用的浏览器端 JS（取 id / name / data-testid / nth-of-type 兜底）。
_FRAME_SELECTOR_JS = """el => {
    if (el.id) return `#${el.id}`;
    if (el.name) return `iframe[name="${el.name}"]`;
    if (el.getAttribute('data-testid')) return `iframe[data-testid="${el.getAttribute('data-testid')}"]`;
    const parent = el.parentNode;
    if (parent) {
        const siblings = Array.from(parent.querySelectorAll('iframe'));
        const index = siblings.indexOf(el);
        if (index !== -1) return `iframe:nth-of-type(${index + 1})`;
    }
    return 'iframe';
}"""


class FlowStep(BaseModel):
    step_number: int = Field(..., description="步骤编号")
    action: str = Field(..., description="动作类型: click/fill/select/hover/dblclick/rightclick/press")
    locator_type: str = Field(..., description="定位策略: role/text/placeholder/xpath/css/coordinate")
    locator_value: str = Field(..., description="定位主参数值")
    locator_extra: str | None = Field(default=None, description="可访问名称（role 定位时）")
    frame_path: list[str] = Field(default_factory=list, description="嵌套 iframe 的链式路径")
    value: str | None = Field(default=None, description="输入或配置的参数")
    description: str = Field(..., description="步骤描述")
    expected_result: str | None = Field(default=None, description="预期结果")


class RecordingSession(BaseModel):
    flow_name: str
    system_under_test: str
    description: str
    steps: list[FlowStep] = Field(default_factory=list)


async def _frame_path(frame) -> list[str]:
    """计算 iframe 链式路径（自外向内），供 FlowStep.frame_path 记录。

    顶层命中时 frame 为 Page 本身（无 parent_frame），返回空列表。
    """
    path: list[str] = []
    current = frame
    while current is not None and getattr(current, "parent_frame", None) is not None:
        try:
            element = await current.frame_element()
            if element is not None:
                selector = await element.evaluate(_FRAME_SELECTOR_JS)
                path.insert(0, selector)
        except Exception:  # noqa: BLE001 - 单层 iframe 元素不可得时降级为忽略
            pass
        current = current.parent_frame
    return path


def _locator_meta(
    role: str | None,
    name: str | None,
    text: str | None,
    placeholder: str | None,
    css: str | None,
    xpath: str | None,
) -> tuple[str, str, str | None]:
    """归一化单一定位维度为 (locator_type, locator_value, locator_extra)。

    优先级对齐 _resolve_locator 的语义定位优先级（role > text > placeholder
    > xpath > css）；无维度时抛 ValueError（交由 _err 收敛为 {"ok": False}）。
    """
    if role is not None:
        return "role", role, name
    if text is not None:
        return "text", text, None
    if placeholder is not None:
        return "placeholder", placeholder, None
    if xpath is not None:
        return "xpath", xpath, None
    if css is not None:
        return "css", css, None
    raise ValueError("need one of: role(+name) / text / placeholder / xpath / css / x,y")


async def _execute_action(page, frame, locator, action_key: str, value: str | None) -> None:
    """执行单条动作，语义对齐 page_interact（复用 _do_fill_with_visual / _antd_select_option）。"""
    if action_key == "click":
        await locator.click(timeout=ELEMENT_WAIT_TIMEOUT_MS)
    elif action_key == "dblclick":
        await locator.dblclick(timeout=ELEMENT_WAIT_TIMEOUT_MS)
    elif action_key == "rightclick":
        await locator.click(button="right", timeout=ELEMENT_WAIT_TIMEOUT_MS)
    elif action_key == "hover":
        await locator.hover(timeout=ELEMENT_WAIT_TIMEOUT_MS)
    elif action_key == "fill":
        if value is None:
            raise ValueError("fill requires value")
        await _do_fill_with_visual(page, locator, value)
    elif action_key == "select":
        if value is None:
            raise ValueError("select requires value")
        # antd Select（div 组合，select_option 无效）→ 一步展开+选 option；
        # 原生 <select> 保持 Playwright select_option。
        is_antd = await locator.evaluate(
            "el => !!el.closest('.ant-select, .ant-cascader, .ant-tree-select')"
        )
        if is_antd:
            await _antd_select_option(page, frame, locator, value)
        else:
            await locator.select_option(value)
    elif action_key == "press":
        if value is None:
            raise ValueError("press requires value")
        await locator.press(value)
    else:
        raise ValueError(f"unknown action {action_key!r}")


@tool(
    title="Recorder: Start Session",
    description="开启测试用例录制会话：创建空的录制会话并存储到当前 MCP 会话态，后续用 execute_and_record 逐条记录操作步骤。",
    icons=[_RECORD_ICON],
    tags={"recorder", "qa"},
)
async def start_recording(
    ctx: Context,
    flow_name: str,
    system_under_test: str = "SCM",
    description: str = "",
) -> dict:
    """开启录制会话。

    Args:
        flow_name: 场景名称（如 基础配置_新增字典项）。
        system_under_test: 被测系统（如 WMS/SCM）。
        description: 本次录制内容说明。
    """
    session = RecordingSession(
        flow_name=flow_name,
        system_under_test=system_under_test,
        description=description,
    )
    await ctx.set_state(SESSION_KEY, session.model_dump())
    return {
        "ok": True,
        "flow_name": flow_name,
        "system_under_test": system_under_test,
        "steps": 0,
    }


@tool(
    title="Recorder: Execute & Record",
    description="在录制会话中执行单条动作并记录最佳解析定位器（语义优先 role > text > placeholder > xpath > css，坐标模式兜底），追加为一条用例步骤。",
    icons=[_RECORD_ICON],
    tags={"recorder", "qa"},
)
async def execute_and_record(
    ctx: Context,
    action: str,
    description: str,
    role: str | None = None,
    name: str | None = None,
    text: str | None = None,
    placeholder: str | None = None,
    css: str | None = None,
    xpath: str | None = None,
    x: float | None = None,
    y: float | None = None,
    value: str | None = None,
    expected_result: str | None = None,
    session: str | None = None,
    in_iframe: bool = True,
) -> dict:
    """执行单条动作并记录步骤。

    Args:
        action: click/fill/select/hover/dblclick/rightclick/press。
        description: 步骤的业务描述。
        role/name: 语义定位（get_by_role；name 取 analyze_current_page 返回的真实值）。
        text/placeholder/xpath/css: 备选定位维度（每次仅提供一个）。
        x/y: 视口绝对坐标（坐标模式，与定位参数互斥）。
        value: fill/select/press 的输入值。
        expected_result: 预期结果（缺省导出时填默认值）。
        session: 目标会话名（多账号场景显式指定；缺省用激活会话）。
        in_iframe: 是否在激活 iframe 内查找（默认 true）。
    """
    session_data = await ctx.get_state(SESSION_KEY)
    if not session_data:
        return {"ok": False, "error": "no active recording session; call start_recording first"}
    session = RecordingSession.model_validate(session_data)

    lc = _lifecycle(ctx)
    try:
        page = await lc.page(session)
    except Exception as exc:
        return _err(exc)

    action_key = action.strip().lower()

    try:
        if x is not None or y is not None:
            if x is None or y is None:
                return {"ok": False, "error": "coordinate mode requires both x and y"}
            if action_key in ("click", "dblclick"):
                fn = page.mouse.dblclick if action_key == "dblclick" else page.mouse.click
                await fn(x, y)
            elif action_key == "hover":
                await page.mouse.move(x, y)
            elif action_key == "rightclick":
                await page.mouse.click(x, y, button="right")
            else:
                return {"ok": False, "error": f"action {action!r} not supported in coordinate mode"}
            locator_type, locator_value, locator_extra = "coordinate", f"{x},{y}", None
            frame_path: list[str] = []
        else:
            locator_type, locator_value, locator_extra = _locator_meta(
                role, name, text, placeholder, css, xpath
            )
            locator, frame = await _resolve_locator(
                page,
                role=role,
                name=name,
                text=text,
                placeholder=placeholder,
                css=css,
                xpath=xpath,
                in_iframe=in_iframe,
                return_frame=True,
            )
            await _execute_action(page, frame, locator, action_key, value)
            frame_path = await _frame_path(frame)
    except Exception as exc:
        return _err(exc)

    step_num = len(session.steps) + 1
    new_step = FlowStep(
        step_number=step_num,
        action=action_key,
        locator_type=locator_type,
        locator_value=locator_value,
        locator_extra=locator_extra,
        frame_path=frame_path,
        value=value,
        description=description,
        expected_result=expected_result,
    )
    session.steps.append(new_step)
    await ctx.set_state(SESSION_KEY, session.model_dump())

    return {
        "ok": True,
        "step_number": step_num,
        "action": action_key,
        "locator_type": locator_type,
        "locator_value": locator_value,
        "locator_extra": locator_extra,
        "frame_path": frame_path,
        "total_steps": len(session.steps),
    }


@tool(
    title="Recorder: Export Session",
    description="结束录制会话，一键打包生成测试资产 JSON（流程名 + 步骤）与 Shadcn 风格 Excel 用例到输出目录，并清空当前录制会话态。",
    icons=[_RECORD_ICON],
    tags={"recorder", "qa"},
)
async def export_session(ctx: Context) -> dict:
    """导出录制会话为 JSON 资产 + Excel 用例（落盘 OUTPUT_DIR）。"""
    data_dict = await ctx.get_state(SESSION_KEY)
    if not data_dict or not data_dict.get("steps"):
        return {"ok": False, "error": "no active recording session with steps to export"}

    filename = (
        str(data_dict.get("flow_name", "unnamed"))
        .replace(" ", "_")
        .replace("/", "_")
        .replace("\\", "_")
    )

    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        json_path = OUTPUT_DIR / f"{filename}_asset.json"
        json_path.write_text(
            json.dumps(data_dict, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        excel_path = OUTPUT_DIR / f"{filename}.xlsx"
        render_shadcn_excel(data_dict, str(excel_path))
    except Exception as exc:
        return _err(exc)

    try:
        await ctx.delete_state(SESSION_KEY)
    except Exception:  # noqa: BLE001 - 会话态清理失败不影响导出结果
        pass

    return {
        "ok": True,
        "json_path": str(json_path),
        "excel_path": str(excel_path),
        "steps": len(data_dict["steps"]),
    }

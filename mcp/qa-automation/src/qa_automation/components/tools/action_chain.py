"""动作链：一次调用顺序执行多个交互动作，链尾统一观察一次。

适配自 qa-automation-plugin 的 execute_action_chain，融入本框架：
- 复用现有执行体（_resolve_locator / _do_fill_with_visual / _antd_select_option），
  不复制定位与输入逻辑；
- 动作集合对齐 page_interact（click/fill/select/press/hover/dblclick/rightclick/check/uncheck）；
- 自动降级：antd 常驻 dropdown 的 nth 变体（hidden 层预检跳过）+ 去重；
- 链尾统一弹层/消息观察 + URL 变化对比（只观察一次，显著减少 AI 往返）。

每步动作 schema：
  {action, role, name, text, placeholder, css, xpath, x, y,
   value, input_method, clear_first, press_enter, key, fallbacks}
  - action: click/fill/select/press/hover/dblclick/rightclick/check/uncheck
  - 定位: role+name / text / placeholder / css / xpath（一个维度）或 x/y 坐标
  - fallbacks: 可选 [{…完整动作参数}] 备用定位，主定位失败按序尝试
  - 自动附加: css 含 li 与 [title=]（antd 下拉选项）时生成 >> nth=0..3 变体
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastmcp import Context
from fastmcp.tools import tool
from mcp.types import Icon

from qa_automation.components.tools.browser import (
    _active_iframe_frame,
    _antd_select_option,
    _detect_focus_layer,
    _do_fill_with_visual,
    _err,
    _lifecycle,
    _resolve_locator,
    _visualize_default,
)

_CHAIN_ICON = Icon(
    src="data:image/svg+xml;base64,PHN2ZyB4bWxucz0naHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmcnIHdpZHRoPScyNCcgaGVpZ2h0PScyNCc+PHBhdGggZD0nTTQgN2wxNi04TTQgMTdsMTYtOCcgZmlsbD0nbm9uZSBzdHJva2U9JyUyMzFmN2FlNCcgc3Ryb2tlLXdpZHRoPScyJy8+PGNpcmNsZSBjeD0nNCcgY3k9JzcnIHI9JzInIGZpbGw9JyUyMzFmN2FlNCcvPjxjaXJjbGUgY3g9JzIwJyBjeT0nMTcnIHI9JzInIGZpbGw9JyUyMzFmN2FlNCcvPjwvc3ZnPg==",
    mime_type="image/svg+xml",
)

_SUPPORTED = {"click", "fill", "select", "press", "hover", "dblclick", "rightclick", "check", "uncheck"}


def _action_key(act: dict) -> str:
    """动作定位指纹：相同定位维度视为同一尝试，用于降级去重。"""
    for dim in ("role", "text", "placeholder", "css", "xpath"):
        if act.get(dim):
            return f"{dim}|{act.get(dim)}|{act.get('name') or ''}"
    return f"xy|{act.get('x')}|{act.get('y')}"


def build_action_fallbacks(act: dict) -> list[dict]:
    """自动生成降级定位方案（确定性规则，低风险）。

    规则：antd 下拉选项点击（click/press + css 含 li 与 [title=]）时生成全部
    nth 位次变体（nth=0..3，排除主定位已用位次）——多 select 的 dropdown 常驻
    DOM（modal 关闭不卸载），激活层位次不定，逐个位次尝试；hidden 层由
    _is_css_nth_visible 预检跳过。主定位已带 nth 时额外生成去 nth 原 selector。
    """
    action = str(act.get("action", "")).lower()
    css = act.get("css")
    if action not in ("click", "press") or not css or "li" not in str(css) or "[title=" not in str(css):
        return []
    sel = str(css)
    used: set[int] = set()
    base = sel
    if ">> nth=" in sel:
        base = sel.split(">> nth=")[0].rstrip()
        try:
            used.add(int(sel.split(">> nth=")[1].strip()))
        except ValueError:
            pass
    fallbacks: list[dict] = []
    for idx in range(4):
        if idx not in used:
            fallbacks.append({**act, "css": f"{base} >> nth={idx}"})
    if ">> nth=" in sel and base:
        fallbacks.append({**act, "css": base})
    return fallbacks


async def _is_css_nth_visible(page, attempt: dict) -> bool:
    """nth 定位变体可见性预检：antd 常驻 dropdown 中 hidden 层直接跳过（不耗等待超时）。"""
    if str(attempt.get("action", "")).lower() not in ("click", "press"):
        return True
    sel = attempt.get("css")
    if not sel or ">> nth=" not in str(sel) or "li" not in str(sel) or "[title=" not in str(sel):
        return True
    try:
        locator = await _resolve_locator(page, css=str(sel))
        if await locator.count() == 0:
            return False
        return await locator.first.is_visible()
    except Exception:  # noqa: BLE001 - 预检异常放行，交由真实定位决定
        return True


async def _run_single(page, act: dict, visualize: bool) -> None:
    """单个动作执行体：定位（含坐标）+ 动作分发（对齐 page_interact 语义）。"""
    action = str(act.get("action", "")).lower()
    if action not in _SUPPORTED:
        raise ValueError(f"action 仅支持 {sorted(_SUPPORTED)}, 收到: {action}")

    x, y = act.get("x"), act.get("y")
    if x is not None or y is not None:
        if x is None or y is None:
            raise ValueError("coordinate mode requires both x and y")
        if action == "click":
            await page.mouse.click(x, y)
        elif action == "dblclick":
            await page.mouse.dblclick(x, y)
        elif action == "rightclick":
            await page.mouse.click(x, y, button="right")
        elif action == "hover":
            await page.mouse.move(x, y)
        else:
            raise ValueError(f"action {action!r} not supported in coordinate mode")
        return

    locator = await _resolve_locator(
        page,
        role=act.get("role"),
        name=act.get("name"),
        text=act.get("text"),
        placeholder=act.get("placeholder"),
        css=act.get("css"),
        xpath=act.get("xpath"),
    )
    if action == "click":
        try:
            await locator.click(timeout=min(int(act.get("timeout_ms", 30_000)), 5000))
        except Exception:
            box = await locator.bounding_box()
            if box is None:
                raise
            await page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    elif action == "dblclick":
        await locator.dblclick()
    elif action == "rightclick":
        await locator.click(button="right")
    elif action == "hover":
        await locator.hover()
    elif action == "fill":
        if act.get("value") is None:
            raise ValueError("fill 动作必须提供 value")
        await _do_fill_with_visual(
            page,
            locator,
            str(act.get("value", "")),
            input_method=str(act.get("input_method", "fill")).lower(),
            clear_first=bool(act.get("clear_first", True)),
            press_enter=bool(act.get("press_enter", False)),
            visualize=visualize,
        )
    elif action == "select":
        if act.get("value") is None:
            raise ValueError("select 动作必须提供 value")
        locator, frame = await _resolve_locator(
            page,
            role=act.get("role"),
            name=act.get("name"),
            text=act.get("text"),
            placeholder=act.get("placeholder"),
            css=act.get("css"),
            xpath=act.get("xpath"),
            return_frame=True,
        )
        is_antd = await locator.evaluate(
            "el => !!el.closest('.ant-select, .ant-cascader, .ant-tree-select')"
        )
        if is_antd:
            await _antd_select_option(page, frame, locator, str(act.get("value", "")))
        else:
            await locator.select_option(str(act.get("value", "")))
    elif action == "press":
        key = act.get("key")
        if not key:
            raise ValueError("press 动作必须提供 key")
        await locator.press(str(key))
    elif action == "check":
        await locator.check()
    elif action == "uncheck":
        await locator.uncheck()


@tool(
    title="Chain: Execute Actions",
    description="批量动作链：一次调用顺序执行多个交互动作（click/fill/select/press/hover/...），"
    "全部完成后统一观察一次并返回（弹层/消息/URL 变化），显著减少 AI 往返。"
    "actions 每项 {action, role, name, text, placeholder, css, xpath, x, y, value, input_method,"
    " clear_first, press_enter, key, fallbacks}；定位信息优先取自 analyze_current_page 输出。"
    "自动降级：css 含 li 与 [title=]（antd 下拉选项）自动生成 nth 位次变体（hidden 层预检跳过）；"
    "显式 fallbacks 按序尝试。stop_on_error=True 首步失败即抛错（含已完成步数），"
    "False 收集 failed 继续执行。",
    icons=[_CHAIN_ICON],
    tags={"browser", "page", "qa", "chain"},
)
async def execute_action_chain(
    ctx: Context,
    actions: list[dict],
    stop_on_error: bool = True,
    visualize: bool | None = None,
    session: str | None = None,
) -> dict:
    """批量执行动作链，链尾统一观察。

    Args:
        actions: 顺序执行的动作列表（见工具 description 的 schema）。
        stop_on_error: True（默认）=首步失败即抛错；False=收集 failed 继续。
        visualize: 光标可视化；缺省读 .env VISUAL_CURSOR_ENABLED。
        session: 目标会话名（多账号场景必须显式指定；缺省使用激活会话）。
    """
    if not actions:
        return {"ok": False, "error": "actions 不能为空"}
    visualize = _visualize_default(visualize)

    lc = _lifecycle(ctx)
    try:
        page = await lc.page(session)
    except Exception as exc:
        return _err(exc)

    if visualize:
        try:
            from qa_automation.browser.visual import VirtualCursor

            await VirtualCursor.attach(page)
        except Exception:  # noqa: BLE001
            visualize = False

    url_before = page.url
    executed = 0
    failed: list[dict] = []
    tried_total = 0
    try:
        for i, act in enumerate(actions):
            action = str(act.get("action", "")).lower()
            attempts: list[dict] = [
                act,
                *(act.get("fallbacks") or []),
                *build_action_fallbacks(act),
            ]
            last_err: Exception | None = None
            tried = 0
            seen: set[str] = set()
            step_failed = False
            for attempt in attempts:
                key = _action_key(attempt)
                if key in seen:
                    continue
                seen.add(key)
                if not await _is_css_nth_visible(page, attempt):
                    continue
                tried += 1
                try:
                    await _run_single(page, attempt, visualize)
                    break
                except Exception as exc:  # noqa: BLE001
                    last_err = exc
            else:
                step_failed = True
            if step_failed:
                err = last_err or RuntimeError("动作缺少有效定位参数")
                if stop_on_error:
                    return {
                        "ok": False,
                        "error": f"动作链第 {i + 1} 步 ({action}) 失败, 已完成 {executed} 步, "
                        f"已尝试 {tried} 个定位方案: {err}",
                        "executed": executed,
                        "failed": [{"index": i + 1, "action": action, "error": str(err)}],
                    }
                failed.append({"index": i + 1, "action": action, "attempts": tried, "error": str(err)})
            else:
                executed += 1
            tried_total += tried
    finally:
        if visualize:
            try:
                from qa_automation.browser.visual import VirtualCursor

                await VirtualCursor.clear(page)
            except Exception:  # noqa: BLE001
                pass

    # 链尾统一观察：等待弹层/消息出现后检测一次 + URL 变化对比
    await asyncio.sleep(1.2)
    observation = await _detect_focus_layer(page)
    url_changed = page.url != url_before
    return {
        "ok": True,
        "status": "success" if not failed else "partial",
        "executed": executed,
        "failed": failed,
        "url_changed": url_changed,
        "url": page.url,
        "observation": observation,
    }

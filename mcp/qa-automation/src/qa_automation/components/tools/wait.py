"""等待条件 MCP 工具：轮询页面直到条件成立或超时，超时返回最后状态快照（不抛协议异常）。

对齐 qa-automation-plugin 的 wait_for_condition_impl，适配本扁平项目：
- browser_mgr -> PlaywrightLifecycle（`page = await lc.page(session)`）；
- frame 解析 -> `_resolve_locator` / `_active_iframe_frame`（顶层优先、激活 iframe 兜底）；
- 条件检查统一手写轮询（不依赖 Playwright `expect` 的版本敏感 API），任意版本可用；
- 超时返回最后一次状态快照，供 QA/Agent 决策，绝不抛错。
"""
from __future__ import annotations

import asyncio
import time

from fastmcp import Context
from fastmcp.tools import tool
from mcp.types import Icon

from qa_automation.components.tools.browser import (
    _active_iframe_frame,
    _err,
    _lifecycle,
    _resolve_locator,
)

WAIT_CONDITIONS = ("element_visible", "element_hidden", "element_has_text", "text_present", "url_contains")

_WAIT_ICON = Icon(
    src="data:image/svg+xml;base64,PHN2ZyB4bWxucz0naHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmcnIHdpZHRoPScyNCcgaGVpZ2h0PScyNCc+PGNpcmNsZSBjeD0nMTInIGN5PScxMycgcj0nOCcgZmlsbD0nbm9uZScgc3Ryb2tlPSd3aGl0ZScgc3Ryb2tlLXdpZHRoPScxLjUnLz48cGF0aCBkPSdNMTIgOXY0bDMgMicgZmlsbD0nbm9uZScgc3Ryb2tlPSd3aGl0ZScgc3Ryb2tlLXdpZHRoPScxLjUnLz48cGF0aCBkPSdNOSAzaDYnIGZpbGw9J25vbmUnIHN0cm9rZT0nd2hpdGUnIHN0cm9rZS13aWR0aD0nMS41Jy8+PC9zdmc+",
    mime_type="image/svg+xml",
)


async def _check_wait_condition(
    page,
    condition: str,
    selector: str | None,
    expected_text: str | None,
    exact: bool,
) -> dict:
    """单次条件检查：返回 {"met": bool, "detail": ...}（供轮询与超时快照复用）。"""
    if condition == "url_contains":
        url = page.url
        return {"met": expected_text in url, "detail": {"url": url}}

    if condition == "text_present":
        # text_present 用 textContent 而非 innerText：innerText 只返回"渲染可见"文本，
        # 对 display:none / 动画中（ant-message move-up-leave 类）的元素读不到，
        # 短命校验消息（3s）极易错过；textContent 返回全部 DOM 文本（含隐藏态）。
        # 帧顺序：激活 iframe 优先（SCM 业务消息多渲染在 iframe 内），再主 frame + 其余。
        frames: list = []
        active = await _active_iframe_frame(page)
        if active is not None:
            frames.append(active)
        frames.append(page.main_frame)
        for f in page.frames:
            if f not in frames:
                frames.append(f)
        for f in frames:
            try:
                body_text = await f.evaluate(
                    "() => document.body ? (document.body.textContent || '') : ''"
                )
            except Exception:
                continue
            if body_text and expected_text in body_text:
                return {"met": True, "detail": {"frame": f.url, "match": True}}
        return {"met": False, "detail": {"frames_checked": len(frames)}}

    # selector 类条件：顶层优先，激活 iframe 兜底（对齐 _resolve_locator 语义）。
    locator, _ = await _resolve_locator(
        page, css=selector, in_iframe=True, return_frame=True
    )
    count = await locator.count()
    if count == 0:
        return {"met": condition == "element_hidden", "detail": {"count": 0}}

    if condition == "element_hidden":
        # 全部匹配项均不可见（或 count==0）才算隐藏 —— 与快照语义一致。
        visibles = 0
        for i in range(count):
            try:
                if await locator.nth(i).is_visible():
                    visibles += 1
            except Exception:
                pass  # 元素 detached -> 视为不可见
        return {"met": visibles == 0, "detail": {"count": count, "visible": visibles}}

    first = locator.first
    visible = await first.is_visible()
    if condition == "element_visible":
        return {"met": visible, "detail": {"count": count, "visible": visible}}

    # element_has_text
    if not visible:
        return {"met": False, "detail": {"count": count, "visible": False}}
    texts = (
        await first.all_inner_texts()
        if count == 1
        else await locator.locator("visible=true").all_inner_texts()
    )
    joined = " ".join(t.strip() for t in texts if t.strip())
    matched = joined == expected_text if exact else expected_text in joined
    return {"met": matched, "detail": {"count": count, "text": joined[:200]}}


@tool(
    title="Wait: For Condition",
    description=(
        "轮询等待页面条件成立（超时返回最后状态快照，不抛错）。"
        "condition 支持：element_visible（selector 可见，默认）、"
        "element_hidden（selector 全部匹配项均不可见或不存在）、"
        "element_has_text（selector 可见文本包含/精确等于 expected_text）、"
        "text_present（目标 frame 或全部 frame 的 DOM 文本出现 expected_text，含隐藏/动画态）、"
        "url_contains（页面 URL 包含 expected_text）。"
        "selector 为 CSS/Playwright 选择器；element_has_text/text_present/url_contains 需提供 expected_text。"
        "典型用法：提交表单后 wait_for_condition(condition='text_present', expected_text='新增成功', timeout_ms=10000)。"
    ),
    icons=[_WAIT_ICON],
    tags={"wait", "assert", "condition"},
)
async def wait_for_condition(
    ctx: Context,
    condition: str = "element_visible",
    selector: str | None = None,
    expected_text: str | None = None,
    exact: bool = False,
    timeout_ms: int = 15000,
    poll_interval_ms: int = 300,
    session: str | None = None,
) -> dict:
    """轮询等待页面条件成立，超时返回最后一次状态快照（不抛异常）。"""
    try:
        if condition not in WAIT_CONDITIONS:
            raise ValueError(f"condition 仅支持: {sorted(WAIT_CONDITIONS)}")
        if condition in ("element_visible", "element_hidden", "element_has_text") and not selector:
            raise ValueError(f"condition={condition} 时必须提供 selector")
        if condition in ("element_has_text", "text_present", "url_contains") and not expected_text:
            raise ValueError(f"condition={condition} 时必须提供 expected_text")
        timeout_ms = max(100, min(int(timeout_ms), 60000))
        poll_interval_ms = max(50, min(int(poll_interval_ms), 2000))

        page = await _lifecycle(ctx).page(session)
        started = time.monotonic()

        async def _snapshot() -> dict:
            try:
                return await _check_wait_condition(
                    page, condition, selector, expected_text, exact
                )
            except Exception as exc:
                return {"met": False, "detail": {"error": f"{type(exc).__name__}: {exc}"}}

        deadline = time.monotonic() + timeout_ms / 1000
        last = {"met": False, "detail": {}}
        while True:
            last = await _snapshot()
            if last.get("met"):
                break
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(poll_interval_ms / 1000)

        return {
            "ok": True,
            "condition": condition,
            "satisfied": bool(last.get("met")),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "state": last.get("detail"),
        }
    except Exception as exc:
        return _err(exc)

"""虚拟光标 + 目标高亮可视化测试：注入、高亮、波纹、完成后 DOM 清除。"""
from __future__ import annotations

from qa_automation.browser.lifecycle import PlaywrightLifecycle
from qa_automation.browser.visual import VirtualCursor
from qa_automation.components.tools.browser import _do_fill_with_visual


async def _shadow_query(page, selector: str) -> bool:
    return await page.evaluate(
        f"() => !!document.getElementById('__qa_mcp_visuals__')"
        f"?.shadowRoot.querySelector({selector!r})"
    )


async def test_virtual_cursor_injection() -> None:
    lc = PlaywrightLifecycle()
    await lc.launch(headless=True)
    try:
        s = await lc.create_session("v", use_default=False)
        page = await s.ensure_page()
        await page.goto("data:text/html,<h1>visual test</h1>")

        await VirtualCursor.attach(page)
        assert await page.evaluate(
            "() => !!document.getElementById('__qa_mcp_visuals__')"
        ) is True
        # 保留：高亮框（呼吸动画）存在
        assert await _shadow_query(page, ".highlight") is True
        # 剔除：标签浮层不存在
        assert await _shadow_query(page, ".label") is False

        # 目标高亮 + 光标
        await VirtualCursor.target(page, 100, 100, 200, 40)
        assert await _shadow_query(page, ".highlight") is True
        assert await _shadow_query(page, "img.cursor.on") is True

        # 点击波纹
        await VirtualCursor.click_at(page, 200, 180)
        assert await _shadow_query(page, ".ripple") is True

        # 交互完成：全部特效图层从 DOM 移除
        await VirtualCursor.clear(page)
        assert await page.evaluate(
            "() => !document.getElementById('__qa_mcp_visuals__')"
        ) is True
    finally:
        await lc.close()


async def test_cursor_reaches_target_before_return() -> None:
    """时序修复：target/moveTo 返回时动画已完成，光标已到目标（真实交互在光标到位后触发）。"""
    lc = PlaywrightLifecycle()
    await lc.launch(headless=True)
    try:
        s = await lc.create_session("v", use_default=False)
        page = await s.ensure_page()
        await page.goto("data:text/html,<h1>timing test</h1>")

        await VirtualCursor.attach(page)
        # 移动到 (200,100)：await 返回即动画完成，光标（热点 5,10）应精确就位
        await VirtualCursor.move_to(page, 200, 100)
        pos = await page.evaluate(
            """() => {
                const el = document.getElementById('__qa_mcp_visuals__').shadowRoot.querySelector('img.cursor');
                const cs = getComputedStyle(el).transform;
                const m = cs && cs !== 'none' ? cs.match(/matrix\\([^)]*,\\s*([-\\d.]+),\\s*([-\\d.]+)\\)/) : null;
                return m ? [Number(m[1]) + 5, Number(m[2]) + 10] : null;
            }"""
        )
        assert pos == [200.0, 100.0], f"cursor not at target after moveTo: {pos}"

        # target（高亮 + 移动到元素中心）：返回后光标在中心
        await VirtualCursor.target(page, 300, 150, 80, 40)
        pos2 = await page.evaluate(
            """() => {
                const el = document.getElementById('__qa_mcp_visuals__').shadowRoot.querySelector('img.cursor');
                const cs = getComputedStyle(el).transform;
                const m = cs && cs !== 'none' ? cs.match(/matrix\\([^)]*,\\s*([-\\d.]+),\\s*([-\\d.]+)\\)/) : null;
                return m ? [Number(m[1]) + 5, Number(m[2]) + 10] : null;
            }"""
        )
        assert pos2 == [340.0, 170.0], f"cursor not at target center: {pos2}"

        # clickAt 已到位（零距离）→ 直接波纹，不触发新移动动画
        await VirtualCursor.click_at(page, 340, 170)
        pos3 = await page.evaluate(
            """() => {
                const el = document.getElementById('__qa_mcp_visuals__').shadowRoot.querySelector('img.cursor');
                const cs = getComputedStyle(el).transform;
                const m = cs && cs !== 'none' ? cs.match(/matrix\\([^)]*,\\s*([-\\d.]+),\\s*([-\\d.]+)\\)/) : null;
                return m ? [Number(m[1]) + 5, Number(m[2]) + 10] : null;
            }"""
        )
        assert pos3 == [340.0, 170.0], f"clickAt moved cursor unexpectedly: {pos3}"
        assert await _shadow_query(page, ".ripple") is True

        await VirtualCursor.clear(page)
        assert await page.evaluate(
            "() => !document.getElementById('__qa_mcp_visuals__')"
        ) is True
    finally:
        await lc.close()


async def test_fill_with_visual_inject_write_and_cleanup() -> None:
    """page_fill/page_interact 的输入执行体：光标注入→写入→交互完成特效移除。"""
    lc = PlaywrightLifecycle()
    await lc.launch(headless=True)
    try:
        s = await lc.create_session("v", use_default=False)
        page = await s.ensure_page()
        await page.goto("data:text/html,<input id='i'>")
        loc = page.locator("#i")

        # type 逐字模式 + visualize：高亮注入、值写入、完成后特效移除
        await _do_fill_with_visual(
            page, loc, "xyz", input_method="type", clear_first=True, visualize=True
        )
        assert await loc.input_value() == "xyz"
        assert await page.evaluate(
            "() => !document.getElementById('__qa_mcp_visuals__')"
        ) is True

        # fill 原生模式 + press_enter（再填充触发 Ctrl+A 无需，fill 天然清空）
        await _do_fill_with_visual(page, loc, "ab", press_enter=True, visualize=True)
        assert await loc.input_value() == "ab"
        assert await page.evaluate(
            "() => !document.getElementById('__qa_mcp_visuals__')"
        ) is True
    finally:
        await lc.close()

from __future__ import annotations

import pytest
from playwright.async_api import BrowserContext

from qa_automation.browser.lifecycle import LifecycleError, PlaywrightLifecycle, SessionError

TEST_URL = "data:text/html,<html><body><h1 id='t'>hello</h1></body></html>"
COOKIE_URL = "http://example.com/"


@pytest.fixture
async def launched() -> PlaywrightLifecycle:
    lc = PlaywrightLifecycle()
    await lc.launch(headless=True)
    yield lc
    await lc.close()


async def test_launch_roundtrip(launched: PlaywrightLifecycle) -> None:
    await launched.create_session("s1", use_default=False)
    page = await launched.page("s1")
    await page.goto(TEST_URL)
    assert "hello" in await page.content()
    assert launched.active_session_name == "s1"
    assert launched.mode == "launch"


async def test_session_cookie_isolation(launched: PlaywrightLifecycle) -> None:
    await launched.create_session("a", use_default=False)
    await launched.create_session("b", use_default=False)
    ctx_a: BrowserContext = launched._sessions["a"].context
    ctx_b: BrowserContext = launched._sessions["b"].context
    await ctx_a.add_cookies([{"name": "session", "value": "a", "url": COOKIE_URL}])
    await ctx_b.add_cookies([{"name": "session", "value": "b", "url": COOKIE_URL}])
    assert (await ctx_a.cookies(COOKIE_URL))[0]["value"] == "a"
    assert (await ctx_b.cookies(COOKIE_URL))[0]["value"] == "b"


async def test_switch_and_close_session(launched: PlaywrightLifecycle) -> None:
    await launched.create_session("a", use_default=False)
    await launched.create_session("b", use_default=False)
    assert launched.active_session_name == "b"
    await launched.switch_session("a")
    assert launched.active_session_name == "a"
    await launched.close_session("a")
    assert "a" not in {s["name"] for s in launched.sessions()}
    assert launched.active_session_name == "b"


async def test_storage_state_roundtrip(
    launched: PlaywrightLifecycle, tmp_path
) -> None:
    await launched.create_session("a", use_default=False)
    ctx_a = launched._sessions["a"].context
    await ctx_a.add_cookies([{"name": "token", "value": "abc", "url": COOKIE_URL}])
    state = tmp_path / "state.json"
    await launched.save_storage_state("a", state)
    await launched.create_session("b", storage_state=str(state), use_default=False)
    ctx_b = launched._sessions["b"].context
    assert (await ctx_b.cookies(COOKIE_URL))[0]["value"] == "abc"


async def test_error_paths(launched: PlaywrightLifecycle) -> None:
    with pytest.raises(SessionError):
        await launched.page()  # 无会话
    await launched.create_session("s", use_default=False)
    with pytest.raises(SessionError):
        await launched.create_session("s")  # 重名
    with pytest.raises(SessionError):
        await launched.switch_session("nope")
    with pytest.raises(SessionError):
        await launched.close_session("nope")


async def test_not_connected() -> None:
    lc = PlaywrightLifecycle()
    with pytest.raises(LifecycleError):
        await lc.page()
    with pytest.raises(LifecycleError):
        await lc.create_session("x")
    with pytest.raises(ValueError):
        await lc.connect(preferred="nope")  # 非法模式


async def test_connect_double() -> None:
    lc = PlaywrightLifecycle()
    await lc.launch(headless=True)
    with pytest.raises(LifecycleError):
        await lc.connect()
    await lc.close()


async def test_default_context_session(cdp_url: str) -> None:
    """默认会话（use_default=True，默认行为）：绑定默认上下文、close 拒绝、close() 不清理。

    不在默认上下文中开页面（避免污染用户浏览器标签页）。
    """
    lc = PlaywrightLifecycle()
    await lc.attach(cdp_url=cdp_url, timeout_ms=10_000)
    session = await lc.create_session("d")  # 不传 use_default -> 默认 True
    assert not session.managed
    assert session.context is lc._default_context
    pages_before = len(lc._default_context.pages)
    with pytest.raises(SessionError):
        await lc.close_session("d")  # 默认上下文受保护
    await lc.close()  # 不应清理默认上下文
    # 再次 attach：默认上下文仍在（close() 未动用户窗口），且未新增页面
    lc2 = PlaywrightLifecycle()
    try:
        await lc2.attach(cdp_url=cdp_url, timeout_ms=10_000)
        assert len(lc2._default_context.pages) == pages_before
    finally:
        await lc2.close()


async def test_default_session_adopts_existing_tab(cdp_url: str) -> None:
    """默认会话 page() 接管已有标签页，不新建空白页。"""
    lc = PlaywrightLifecycle()
    await lc.attach(cdp_url=cdp_url, timeout_ms=10_000)
    try:
        pages_before = len(lc._default_context.pages)
        assert pages_before >= 1  # 浏览器至少有一个已开标签页
        session = await lc.create_session("adopt")
        page = await session.ensure_page()
        # 未新建标签页：接管的是已有页面
        assert len(lc._default_context.pages) == pages_before
        assert page in lc._default_context.pages
    finally:
        await lc.close()


async def test_attach_9222_smoke(cdp_url: str) -> None:
    """接管浏览器：建隔离会话、不污染默认上下文、清理后仍可再次接管。"""
    lc = PlaywrightLifecycle()
    await lc.attach(cdp_url=cdp_url, timeout_ms=10_000)
    assert lc.mode == "attach"
    default_ctx = lc._default_context
    assert default_ctx is not None
    try:
        await lc.create_session("iso", use_default=False)
        iso_ctx = lc._sessions["iso"].context
        await iso_ctx.add_cookies([{"name": "iso_only", "value": "1", "url": COOKIE_URL}])
        assert (await iso_ctx.cookies(COOKIE_URL))[0]["name"] == "iso_only"
        assert "iso_only" not in {c["name"] for c in await default_ctx.cookies(COOKIE_URL)}
    finally:
        await lc.close()
    # 浏览器进程仍在：可再次 attach 且能看到页面
    lc2 = PlaywrightLifecycle()
    try:
        await lc2.attach(cdp_url=cdp_url, timeout_ms=10_000)
        assert len(lc2._default_context.pages) >= 1
    finally:
        await lc2.close()

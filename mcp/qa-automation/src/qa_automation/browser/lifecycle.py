"""Playwright 浏览器生命周期管理器。

策略（已与需求方确认）：
- 默认 attach 接管 9222 端口已打开的 Chrome（日常浏览器），`no_defaults=True`
  不向用户的默认会话强加 Playwright 的下载/聚焦/媒体模拟覆盖；
- launch 为兜底：自启带 Playwright 标准参数的 Chrome（独立临时 profile）；
- 会话 = 全新空 cookie 的隔离 BrowserContext（实测 CDP 连接下同样隔离）；
- 清理只针对本管理器创建的会话；attach 模式下绝不动用户的默认上下文与浏览器进程。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from qa_automation.config import CDP_URL as DEFAULT_CDP_URL

# Playwright 标准 Chromium 启动开关（摘自 playwright-core 1.62.0 chromiumSwitches.ts，
# 去除 Linux/macOS/Edge 专属项），供 launch 兜底模式使用。
DEFAULT_LAUNCH_ARGS: tuple[str, ...] = (
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-networking",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-back-forward-cache",
    "--disable-breakpad",
    "--disable-client-side-phishing-detection",
    "--disable-component-update",
    "--disable-default-apps",
    "--disable-extensions",
    "--disable-hang-monitor",
    "--disable-ipc-flooding-protection",
    "--disable-popup-blocking",
    "--disable-prompt-on-repost",
    "--disable-renderer-backgrounding",
    "--disable-search-engine-choice-screen",
    "--disable-sync",
    "--force-color-profile=srgb",
    "--metrics-recording-only",
    "--password-store=basic",
    "--no-service-autorun",
    "--export-tagged-pdf",
    "--disable-infobars",
)


class LifecycleError(RuntimeError):
    """生命周期状态错误：未连接、重复连接、连接失败等。"""


def _friendly_attach_error(url: str, exc: BaseException) -> str:
    """将底层 Playwright/CDP 连接错误转成可操作的诊断信息。"""
    text = str(exc)
    if "service_worker" in text or "browserContextId" in text or "Assertion error" in text:
        return (
            f"attach to {url} failed: MV3 扩展（如 ScriptCat）的 service worker 干扰 CDP "
            f"连接（Playwright 已知问题，assert on targetInfo.browserContextId）。请在浏览器 "
            f"chrome://extensions 中禁用/卸载该扩展后重试；或改用 mode=launch 自启浏览器。"
            f"详情: {text[:160]}"
        )
    if "ECONNREFUSED" in text or "Connection refused" in text:
        return (
            f"attach to {url} failed: 目标浏览器未在监听（请确认已以 "
            f"--remote-debugging-port 启动浏览器）。详情: {text[:160]}"
        )
    return f"attach to {url} failed: {text[:300]}"


class SessionError(ValueError):
    """会话错误：重名、不存在、无激活会话。"""


@dataclass
class Session:
    """一个命名会话，对应一个 BrowserContext。

    managed=True: 自建隔离上下文（close 时清理）。
    managed=False: 用户浏览器默认上下文（可见窗口；close 时保护，不清理）。
    account: 绑定的账号名（session_open_isolated 等传入），用于多账号路由防串窗。
    """

    name: str
    context: BrowserContext
    managed: bool = True
    account: str | None = None
    created_at: float = field(default_factory=time.time)
    page: Page | None = None

    async def ensure_page(self) -> Page:
        """返回会话页面；无则接管上下文中的激活标签页，仍无才新建。

        接管激活标签页：document.visibilityState == 'visible' 即前台（用户当前
        正在看的标签页）；后台标签页为 hidden。避免在已有业务页时新建空白页。
        """
        if self.page is not None and not self.page.is_closed():
            return self.page
        for candidate in list(self.context.pages):
            if candidate.is_closed():
                continue
            try:
                if await candidate.evaluate("document.visibilityState") == "visible":
                    self.page = candidate
                    return candidate
            except Exception:  # noqa: BLE001 - 页面不可评估则跳过
                continue
        # 无前台激活页 → 取第一个可用页面（不新建）
        for candidate in list(self.context.pages):
            if not candidate.is_closed():
                self.page = candidate
                return candidate
        # 上下文完全没有页面 → 新建
        self.page = await self.context.new_page()
        return self.page


class PlaywrightLifecycle:
    """管理浏览器连接与隔离会话的生命周期（async API）。

    典型用法::

        lc = PlaywrightLifecycle()
        await lc.connect()                 # attach 9222；失败自动 launch 兜底
        await lc.create_session("admin")   # 全新空 cookie 会话
        page = await lc.page("admin")
        await page.goto("https://wms.example.com")
        ...
        await lc.close()                   # 只清理自建会话，不动用户浏览器
    """

    def __init__(self, cdp_url: str = DEFAULT_CDP_URL) -> None:
        self._cdp_url = cdp_url
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._mode: Literal["attach", "launch"] | None = None
        self._default_context: BrowserContext | None = None
        self._sessions: dict[str, Session] = {}
        self._active: str | None = None
        self._last_attach_error: str | None = None
        # 生命周期事件日志（时间戳 + 描述），用于核对浏览器/连接的每一次操作
        self._events: list[tuple[float, str]] = []

    def _log(self, message: str) -> None:
        self._events.append((time.time(), message))
        if len(self._events) > 200:
            self._events = self._events[-200:]

    @property
    def events(self) -> list[str]:
        """最近生命周期事件（ISO 时间戳 + 描述）。"""
        import datetime

        return [
            f"{datetime.datetime.fromtimestamp(ts).isoformat(timespec='seconds')} {msg}"
            for ts, msg in self._events[-20:]
        ]

    # ---------- 连接 ----------

    @property
    def mode(self) -> str | None:
        """当前连接模式：attach（接管已有浏览器）或 launch（自启）。"""
        return self._mode

    @property
    def is_connected(self) -> bool:
        return self._browser is not None

    @property
    def last_attach_error(self) -> str | None:
        """最近一次 attach 失败原因（auto 模式降级 launch 时用于诊断）。"""
        return self._last_attach_error

    async def connect(self, preferred: str = "auto") -> str:
        """建立连接。

        preferred:
        - "auto"（默认）：先 attach 9222，失败则 launch 兜底；
        - "attach"：只尝试接管现有浏览器，失败即抛错；
        - "launch"：直接自启浏览器。
        """
        if self.is_connected:
            raise LifecycleError("already connected")
        if preferred == "auto":
            try:
                await self.attach()
                return "attach"
            except Exception as exc:
                self._last_attach_error = f"{type(exc).__name__}: {exc}"
                await self.launch()
                return "launch"
        if preferred == "attach":
            await self.attach()
            return "attach"
        if preferred == "launch":
            await self.launch()
            return "launch"
        raise ValueError(f"unknown preferred mode: {preferred!r}")

    async def attach(self, cdp_url: str | None = None, timeout_ms: int = 30_000) -> None:
        """接管已在 cdp_url 打开的 Chrome（默认 http://127.0.0.1:9222）。"""
        if self.is_connected:
            raise LifecycleError("already connected")
        url = cdp_url or self._cdp_url
        pw = await async_playwright().start()
        try:
            browser = await pw.chromium.connect_over_cdp(
                url, is_local=True, no_defaults=True, timeout=timeout_ms
            )
        except Exception as exc:
            await pw.stop()
            self._log(f"attach failed: {url}")
            raise LifecycleError(_friendly_attach_error(url, exc)) from exc
        self._playwright = pw
        self._browser = browser
        self._mode = "attach"
        self._default_context = browser.contexts[0] if browser.contexts else None
        self._log(f"attached: {url}")

    async def launch(
        self,
        *,
        headless: bool = False,
        channel: str = "chrome",
        args: list[str] | None = None,
    ) -> None:
        """兜底：自启带 Playwright 标准参数的 Chrome（独立临时 profile）。"""
        if self.is_connected:
            raise LifecycleError("already connected")
        pw = await async_playwright().start()
        try:
            browser = await pw.chromium.launch(
                channel=channel,
                headless=headless,
                args=list(DEFAULT_LAUNCH_ARGS if args is None else args),
            )
        except Exception:
            await pw.stop()
            raise
        self._playwright = pw
        self._browser = browser
        self._mode = "launch"
        self._default_context = browser.contexts[0] if browser.contexts else None
        self._log("launched browser")

    # ---------- 会话 ----------

    @property
    def active_session_name(self) -> str | None:
        return self._active

    def sessions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": s.name,
                "pages": len(s.context.pages),
                "active": s.name == self._active,
                "default": not s.managed,
                "account": s.account,
            }
            for s in self._sessions.values()
        ]

    async def create_session(
        self,
        name: str,
        storage_state: str | Path | dict | None = None,
        *,
        use_default: bool = True,
        account: str | None = None,
        **context_options: Any,
    ) -> Session:
        """新建命名会话。

        use_default=True（默认）：在浏览器默认上下文（用户已打开窗口）中操作，
            登录/导航对用户可见；该会话受保护，close_session 会拒绝。
            storage_state 通过 cookies 注入恢复（免验证码复用）。
        use_default=False：创建全新空 cookie 的隔离上下文（多账号会话分离），
            storage_state 由 new_context() 原生恢复。
        account: 绑定的账号名（多账号路由防串窗，session_list 可查）。
        context_options: 透传给 browser.new_context()（仅隔离模式）。
        """
        self._ensure_connected()
        if name in self._sessions:
            raise SessionError(f"session {name!r} already exists")
        if use_default:
            if self._mode == "launch":
                # 自启浏览器：无"用户已打开窗口"；所有上下文均在可见浏览器窗口内，
                # 按常规上下文创建（管理权归本管理器）
                context = await self._browser.new_context(
                    storage_state=storage_state, **context_options
                )
                session = Session(name=name, context=context, managed=True, account=account)
            else:
                context = self._default_context
                if context is None:
                    raise SessionError(
                        "no default context available; attach to an existing browser first"
                    )
                if storage_state is not None:
                    await self._apply_storage_state(context, storage_state)
                session = Session(
                    name=name, context=context, managed=False, account=account
                )
        else:
            context = await self._browser.new_context(
                storage_state=storage_state, **context_options
            )
            session = Session(name=name, context=context, managed=True, account=account)
        self._sessions[name] = session
        self._active = name
        self._log(f"session created: {name} (default={use_default}, account={account})")
        return session

    async def switch_session(self, name: str) -> None:
        """切换激活会话（后续 page()/save_storage_state() 缺省作用于它）。"""
        self._ensure_connected()
        if name not in self._sessions:
            raise SessionError(f"session {name!r} not found")
        self._active = name
        self._log(f"session switched: {name}")

    async def _close_context_safely(self, session: Session) -> None:
        """关闭会话的 context，兼容 Helium 定制 Chromium。

        实测：该 Chromium 在 dispose 一个含活动页面的 incognito context 时会把
        整个浏览器退出；必须先关闭 context 内所有页面再 dispose。
        """
        if session.context.is_closed():
            return
        for page in list(session.context.pages):
            if not page.is_closed():
                await page.close()
        await session.context.close()

    async def close_session(self, name: str) -> None:
        """关闭会话及其页面。

        用户默认上下文会话（use_default=True）受保护，拒绝关闭——
        清理只会关闭自建会话；attach 模式断开见 close()。
        """
        self._ensure_connected()
        session = self._sessions.pop(name, None)
        if session is None:
            raise SessionError(f"session {name!r} not found")
        if not session.managed:
            self._sessions[name] = session  # 放回：默认上下文不可关闭
            raise SessionError(
                f"session {name!r} is the browser's default context and cannot be closed"
            )
        await self._close_context_safely(session)
        if self._active == name:
            self._active = next(iter(self._sessions), None)
        self._log(f"session closed: {name}")

    async def page(self, name: str | None = None) -> Page:
        """返回会话的当前页；无则新建。缺省使用激活会话。"""
        self._ensure_connected()
        session = self._session(name)
        return await session.ensure_page()

    def context(self, name: str | None = None) -> BrowserContext:
        """返回会话的 BrowserContext（供 cookies 注入等操作）。缺省使用激活会话。"""
        self._ensure_connected()
        return self._session(name).context

    async def set_session_window_state(
        self, name: str | None = None, window_state: str = "fullscreen"
    ) -> None:
        """设置会话页面所在窗口的状态（CDP Browser.setWindowBounds）。

        window_state: normal/maximized/minimized/fullscreen。
        用于隔离上下文（无痕窗口）全屏展示。
        """
        self._ensure_connected()
        session = self._session(name)
        page = session.context.pages[0] if session.context.pages else await session.ensure_page()
        cdp = await session.context.new_cdp_session(page)
        win = await cdp.send("Browser.getWindowForTarget")
        await cdp.send(
            "Browser.setWindowBounds",
            {"windowId": win["windowId"], "bounds": {"windowState": window_state}},
        )
        self._log(f"window {window_state}: {session.name}")

    async def list_tabs(self, name: str | None = None) -> list[dict]:
        """列出会话上下文中的所有标签页（url/title/是否前台激活）。"""
        self._ensure_connected()
        session = self._session(name)
        tabs: list[dict] = []
        for page in session.context.pages:
            if page.is_closed():
                continue
            visibility = "?"
            title = ""
            try:
                visibility = await page.evaluate("document.visibilityState")
            except Exception:  # noqa: BLE001
                pass
            try:
                title = await page.title()
            except Exception:  # noqa: BLE001
                pass
            tabs.append(
                {
                    "index": len(tabs),
                    "url": page.url or "",
                    "title": title,
                    "active": visibility == "visible",
                }
            )
        return tabs

    async def switch_tab(
        self,
        name: str | None = None,
        index: int | None = None,
        url_contains: str | None = None,
    ) -> Page:
        """将会话切换到指定标签页并前台激活（bring_to_front）。

        index: 标签页序号（list_tabs 的 index）。
        url_contains: 按 URL 子串匹配标签页（优先于 index）。
        """
        self._ensure_connected()
        session = self._session(name)
        tabs = await self.list_tabs(name)
        if not tabs:
            raise SessionError("no tabs in session context")
        if url_contains:
            match = next((t for t in tabs if url_contains in t["url"]), None)
            if match is None:
                raise SessionError(f"no tab matching url contains {url_contains!r}")
            page = session.context.pages[match["index"]]
        else:
            idx = index if index is not None else 0
            if not 0 <= idx < len(tabs):
                raise SessionError(f"tab index {idx} out of range (0-{len(tabs)-1})")
            page = session.context.pages[idx]
        await page.bring_to_front()
        session.page = page
        self._log(f"tab switched: {session.name} -> {(page.url or '')[:60]}")
        return page

    # ---------- 账号态 ----------

    async def save_storage_state(self, name: str, path: str | Path) -> str:
        """将会话的 cookie/localStorage 等账号态落盘，供 create_session 恢复。"""
        session = self._session(name)
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        await session.context.storage_state(path=str(dest))
        self._log(f"state saved: {name} -> {dest}")
        return str(dest)

    # ---------- 状态与清理 ----------

    def summary(self) -> dict[str, Any]:
        info: dict[str, Any] = {
            "mode": self._mode,
            "active": self._active,
            "sessions": self.sessions(),
            "events": self.events,
        }
        if self._browser is not None:
            try:
                info["browser_version"] = self._browser.version
            except Exception:
                pass
        return info

    async def close(self) -> None:
        """关闭本管理器创建的所有会话并断开/关闭浏览器。

        attach 模式：仅清理自建会话；不调用 browser.close()——对用户浏览器
        对象零操作，连接由 pw.stop() 自然断开（实测断开不影响浏览器进程）。
        launch 模式：关闭自启浏览器。
        单个会话关闭失败不阻断整体清理（防止清理链中断导致残留）。
        """
        for session in list(self._sessions.values()):
            if session.managed:
                try:
                    await self._close_context_safely(session)
                except Exception:  # noqa: BLE001 - 会话清理失败不阻断
                    self._log(f"session cleanup failed (ignored): {session.name}")
        self._sessions.clear()
        self._active = None
        if self._mode == "launch" and self._browser is not None:
            try:
                await self._browser.close()
            except Exception:  # noqa: BLE001
                self._log("browser close failed (ignored)")
        self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:  # noqa: BLE001
                pass
            self._playwright = None
        self._mode = None
        self._default_context = None
        self._log("lifecycle closed")

    # ---------- 内部 ----------

    @staticmethod
    async def _apply_storage_state(context: BrowserContext, storage_state: str | Path | dict) -> None:
        """默认上下文模式下恢复账号态：将 storage_state 中的 cookies 注入上下文。

        隔离模式由 new_context(storage_state=...) 原生处理；默认上下文（用户浏览器）
        不能重建，故用 add_cookies 注入（localStorage 部分暂不支持，属已知边界）。
        """
        import json as _json

        if isinstance(storage_state, dict):
            data = storage_state
        else:
            path = Path(storage_state)
            if not path.exists():
                raise SessionError(f"storage_state file not found: {path}")
            data = _json.loads(path.read_text(encoding="utf-8"))
        cookies = data.get("cookies") or []
        if cookies:
            await context.add_cookies(cookies)

    def _ensure_connected(self) -> None:
        if self._browser is None:
            raise LifecycleError("not connected; call connect()/attach()/launch() first")

    def _session(self, name: str | None) -> Session:
        key = name or self._active
        if key is None:
            raise SessionError("no session; call create_session() first or pass a name")
        try:
            return self._sessions[key]
        except KeyError:
            raise SessionError(f"session {key!r} not found") from None

"""浏览器控制 MCP 工具集：接管现有 Chrome、隔离会话、页面交互、账号态存取。

基于 FastMCP 3.x 新特性构建：
- FileSystemProvider 独立 @tool 装饰器，组件与服务器零耦合；
- Context 依赖注入（ctx 类型注解自动注入，参数不进 MCP schema）；
- ctx.info() 客户端日志（MCP Context 特性）；
- 工具 icons（MCP Icon 类型，data URI 内嵌）；
- 统一返回 {"ok": bool, ...} 结构，错误不抛协议异常，便于 QA 断言。
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from fastmcp import Context
from fastmcp.tools import tool
from mcp.types import Icon

from qa_automation.browser.lifecycle import PlaywrightLifecycle

# 弹层聚焦检测 JS（与 analyze_current_page 共用）：modal/下拉/消息（antd portal）
from qa_automation.components.tools.analyze import _FOCUS_LAYER_JS
from qa_automation.config import AUTH_DIR, PROJECT_ROOT, SCREENSHOT_DIR

_ENV_LOADED = False


def _load_env_file() -> None:
    """将项目根 .env 键值写入 os.environ（FastMCP 的 pydantic-settings 加载不一定进 environ）。"""
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _visualize_default(visualize: bool | None) -> bool:
    """解析光标可视化开关：显式参数优先，未传读 .env VISUAL_CURSOR_ENABLED（默认 false）。"""
    if visualize is not None:
        return visualize
    _load_env_file()
    return os.environ.get("VISUAL_CURSOR_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )

_BROWSER_ICON = Icon(
    src="data:image/svg+xml;base64,PHN2ZyB4bWxucz0naHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmcnIHdpZHRoPScyNCcgaGVpZ2h0PScyNCc+PGNpcmNsZSBjeD0nMTInIGN5PScxMicgcj0nMTAnIGZpbGw9JyUyMzM0YTg1MycvPjxwYXRoIGQ9J000IDEyaDE2TTEyIDRjMyAyLjUgNC41IDUgNC41IDhzLTEuNSA1LjUtNC41IDhjLTMtMi41LTQuNS01LTQuNS04UzkgNi41IDEyIDR6JyBmaWxsPSdub25lJyBzdHJva2U9J3doaXRlJyBzdHJva2Utd2lkdGg9JzEuNScvPjwvc3ZnPg==",
    mime_type="image/svg+xml",
)


def _lifecycle(ctx: Context) -> PlaywrightLifecycle:
    return ctx.lifespan_context["lifecycle"]


def _err(exc: Exception) -> dict:
    return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


async def _active_iframe_frame(page):
    """返回激活 tabpanel（role=tabpanel 且 aria-hidden=false）内的 iframe Frame；无则 None。"""
    try:
        iframe_loc = page.locator(
            '.ant-tabs-tabpane[role="tabpanel"][aria-hidden="false"] iframe'
        )
        if await iframe_loc.count() == 0:
            return None
        frame_el = iframe_loc.first
        name = await frame_el.get_attribute("name") or ""
        src = await frame_el.get_attribute("src") or ""
        for f in page.frames:
            if (name and f.name == name) or (not name and src and f.url == src):
                return f
        return await frame_el.content_frame()
    except Exception:  # noqa: BLE001
        return None


async def _detect_focus_layer(page) -> dict | None:
    """检测可见弹层/消息：激活 iframe 优先，再顶层（业务页 portal 渲染在 iframe 内）。"""
    frame = await _active_iframe_frame(page)
    if frame is not None:
        try:
            layer = await frame.evaluate(_FOCUS_LAYER_JS)
            if layer:
                return {**layer, "scope": "iframe"}
        except Exception:  # noqa: BLE001
            pass
    try:
        layer = await page.evaluate(_FOCUS_LAYER_JS)
        if layer:
            return {**layer, "scope": "top"}
    except Exception:  # noqa: BLE001
        pass
    return None


async def _observe_layers(page) -> dict | None:
    """交互后观察浮层/弹窗/消息（antd portal），轮询最长 3s，供 QA 断言。"""
    for _ in range(3):
        await asyncio.sleep(1.0)
        layer = await _detect_focus_layer(page)
        if layer:
            return layer
    return None


async def _do_fill_with_visual(
    page,
    locator,
    value: str,
    input_method: str = "fill",
    clear_first: bool = True,
    press_enter: bool = False,
    visualize: bool = False,
) -> None:
    """输入执行体（对齐 qa-automation-plugin _do_fill）：点击聚焦 + 视觉 + 输入。

    - 先 locator.click() 聚焦（对齐人工操作，触发组件 focus/激活逻辑）；
    - input_method=fill（默认）: Playwright 原生填充，快且稳，自动清空旧值并触发 input 事件；
    - input_method=type: 模拟人工打字（Ctrl+A 清空 → 逐字 0.1s 间隔），
      适用于监听键盘事件的组件；
    - press_enter=True: 输入完成后按回车（搜索框/确认输入场景）。
    """
    box = None
    if visualize:
        try:
            box = await locator.bounding_box()
            if box is not None:
                from qa_automation.browser.visual import VirtualCursor

                await VirtualCursor.attach(page)
                await VirtualCursor.target(
                    page, box["x"], box["y"], box["width"], box["height"]
                )
        except Exception:  # noqa: BLE001 - 特效注入失败不影响输入
            pass
    try:
        await locator.click()  # 先点击聚焦（触发组件的 focus 逻辑）
        if visualize and box is not None:
            try:
                from qa_automation.browser.visual import VirtualCursor

                # 聚焦点击伴随波纹反馈（光标已到位，零距离直接波纹）
                await VirtualCursor.click_at(
                    page, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                )
                await asyncio.sleep(0.65)
            except Exception:  # noqa: BLE001
                pass
        if input_method == "type":
            if clear_first:
                await locator.press("Control+A")
                await locator.press("Backspace")
            if value:
                try:
                    await locator.press_sequentially(value, delay=100)
                except AttributeError:
                    await page.keyboard.type(value, delay=100)
        else:
            await locator.fill(value)
        if press_enter:
            await locator.press("Enter")
    finally:
        # 输入完成（含异常路径）：特效从 DOM 移除
        if visualize:
            try:
                from qa_automation.browser.visual import VirtualCursor

                await VirtualCursor.clear(page)
            except Exception:  # noqa: BLE001
                pass


async def _click_option_in(dropdown, option_text: str) -> bool:
    """在下拉层内按文本定位选项并点击（对齐 qa-automation-plugin）。

    匹配优先级：a11y role=option / menuitem（antd3 用 menuitem，antd4 用 option）
    精确 → 选项文本精确 → 唯一子串。
    歧义（多个候选同时包含目标文本，如"含过敏原"vs"不含过敏原"）时抛出
    带完整候选列表的 RuntimeError，让调用方（Agent）拿到可决策信息，
    而不是盲目点击第一个（会误选）。
    """
    for role in ("option", "menuitem"):
        role_opt = dropdown.get_by_role(role, name=option_text, exact=True).first
        if await role_opt.count() > 0:
            await role_opt.click()
            return True

    candidates = dropdown.locator(
        ".ant-select-item-option, "
        ".ant-select-dropdown-menu-item, "
        ".ant-select-tree-treenode, "
        ".ant-cascader-menu-item"
    )
    if await candidates.count() == 0:
        return False

    texts = await candidates.evaluate_all(
        "els => els.map(e => (e.innerText || e.textContent || '').trim())"
    )
    exact_idx = [i for i, t in enumerate(texts) if t == option_text]
    if len(exact_idx) == 1:
        await candidates.nth(exact_idx[0]).click()
        return True
    if len(exact_idx) > 1:
        raise RuntimeError(
            f"Ant Design 下拉选项 '{option_text}' 存在多个精确匹配, "
            f"候选: {sorted(set(texts))[:20]}"
        )

    sub_idx = [i for i, t in enumerate(texts) if t and option_text in t]
    if len(sub_idx) == 1:
        await candidates.nth(sub_idx[0]).click()
        return True
    if len(sub_idx) > 1:
        raise RuntimeError(
            f"Ant Design 下拉选项 '{option_text}' 匹配到多个候选: "
            f"{sorted(set(texts))[:20]}, 请使用完整选项文本"
        )
    return False


async def _antd_select_option(page, frame, trigger_locator, option_text: str) -> None:
    """Ant Design Select 一步选择：click 展开 → 最新可见 dropdown → 按文本选 option。

    点击目标提升到 .ant-select 容器（antd 内部 input 常 opacity:0/pointer-events:none，
    点击 input 不展开）。连续下拉场景（挨个选下拉）存在动画竞态：上一个下拉的
    收起动画未结束前元素仍 :visible，.last 可能命中残留下拉。处理：找不到目标
    选项时，重新定位最新可见下拉重试，直至新下拉挂载（最多 4 次）。
    """
    container = trigger_locator.locator(
        "xpath=ancestor-or-self::*[contains(@class,'ant-select') "
        "or contains(@class,'ant-cascader') or contains(@class,'ant-tree-select')][1]"
    )
    await container.click()
    for attempt in range(4):
        dropdown = frame.locator(
            ".ant-select-dropdown:visible, "
            ".ant-cascader-dropdown:visible, "
            ".ant-cascader-menus:visible"
        ).last
        try:
            await dropdown.wait_for(
                state="visible",
                timeout=3500 if attempt == 0 else 1500,
            )
        except Exception:
            if attempt == 3:
                raise RuntimeError(f"Ant Design 下拉未展开（选项: {option_text}）")
            await asyncio.sleep(0.4)
            continue
        if await _click_option_in(dropdown, option_text):
            return
        await asyncio.sleep(0.4)
    raise RuntimeError(f"Ant Design 下拉选项不存在: {option_text}")


async def _resolve_locator(
    page,
    *,
    role: str | None = None,
    name: str | None = None,
    text: str | None = None,
    placeholder: str | None = None,
    css: str | None = None,
    xpath: str | None = None,
    in_iframe: bool = True,
    return_frame: bool = False,
):
    """按语义定位优先级构造 Playwright Locator（AI agent 推荐策略）。

    优先级：get_by_role(role, name) → get_by_text(text) → get_by_placeholder
    → xpath → css（结构兜底）。仅允许一个定位维度。
    in_iframe=True（默认）：顶层文档找不到时，降级到激活 tabpanel 的 iframe
    内查找（SCM 业务控件位于激活 iframe 中）；False 仅搜顶层。
    return_frame=True：返回 (locator, frame)——frame 为命中元素所在 Frame
    （顶层为 page 本身；iframe 内为对应 Frame），供 portal 浮层（下拉/日历）
    在相同上下文定位。
    """
    provided = sum(
        1 for v in (role, text, placeholder, css, xpath) if v is not None
    )
    if provided == 0:
        raise ValueError("need one of: role(+name) / text / placeholder / xpath / css")
    if provided > 1:
        raise ValueError(
            "provide exactly one locator dimension (role/text/placeholder/xpath/css)"
        )

    def build(target):
        if role is not None:
            if name:
                return target.get_by_role(role, name=name, exact=False)
            return target.get_by_role(role)
        if text is not None:
            return target.get_by_text(text, exact=False)
        if placeholder is not None:
            return target.get_by_placeholder(placeholder)
        if xpath is not None:
            return target.locator(f"xpath={xpath}")
        return target.locator(css)

    locator = build(page)
    if await locator.count() > 0:
        return (locator, page) if return_frame else locator
    # 顶层未命中 → 激活 iframe 内查找
    if in_iframe:
        frame = await _active_iframe_frame(page)
        if frame is not None:
            frame_locator = build(frame)
            if await frame_locator.count() > 0:
                return (frame_locator, frame) if return_frame else frame_locator
    return (locator, page) if return_frame else locator


@tool(
    title="Browser: Connect",
    description="接管已打开的浏览器（默认 http://127.0.0.1:9222）或自启 Chrome。mode=auto 先尝试接管，失败自动自启兜底。",
    icons=[_BROWSER_ICON],
    tags={"browser", "lifecycle"},
)
async def browser_connect(
    ctx: Context,
    mode: str = "auto",
    cdp_url: str | None = None,
    headless: bool = False,
) -> dict:
    """接管或自启浏览器。

    Args:
        mode: auto=接管优先失败自启; attach=仅接管现有浏览器; launch=直接自启。
        cdp_url: 接管目标地址（仅 attach/auto 使用）；缺省读环境变量 CDP_URL（默认 9222）。
        headless: 自启模式是否无头（接管模式忽略）。
    """
    lc = _lifecycle(ctx)
    if lc.is_connected:
        return {"ok": True, "connected": True, "mode": lc.mode}
    cdp_url = cdp_url or os.environ.get("CDP_URL", "http://127.0.0.1:9222")

    if mode == "launch":
        try:
            await lc.launch(headless=headless)
        except Exception as exc:
            return _err(exc)
        await ctx.info("browser launched (fallback mode)")
        return {"ok": True, "connected": True, "mode": "launch"}

    try:
        await lc.attach(cdp_url=cdp_url)
    except Exception as exc:
        if mode == "attach":
            return _err(exc)
        try:
            await lc.launch(headless=headless)
        except Exception as exc2:
            return {"ok": False, "error": f"attach failed ({exc}); launch failed ({exc2})"}
        await ctx.info(f"attach to {cdp_url} failed, launched browser instead")
        return {"ok": True, "connected": True, "mode": "launch", "attach_error": str(exc)}

    await ctx.info(f"attached to {cdp_url}")
    return {"ok": True, "connected": True, "mode": "attach"}


@tool(
    title="Browser: Status",
    description="浏览器连接与会话状态摘要。",
    icons=[_BROWSER_ICON],
    tags={"browser"},
)
async def browser_status(ctx: Context) -> dict:
    """查看连接模式、浏览器版本、会话列表与激活会话。"""
    lc = _lifecycle(ctx)
    if not lc.is_connected:
        return {"ok": True, "connected": False, "attach_error": lc.last_attach_error}
    return {"ok": True, "connected": True, **lc.summary()}


@tool(
    title="Browser: Disconnect",
    description="关闭所有自建会话并断开连接。接管模式下用户浏览器进程保留。",
    icons=[_BROWSER_ICON],
    tags={"browser", "lifecycle"},
)
async def browser_disconnect(ctx: Context) -> dict:
    """断开浏览器：自建会话全部关闭，用户浏览器不受影响。"""
    lc = _lifecycle(ctx)
    await lc.close()
    await ctx.info("browser disconnected; own sessions closed, user browser kept")
    return {"ok": True, "connected": False}


@tool(
    title="Session: Open Isolated Window",
    description="创建隔离会话（无痕窗口）：cookie/session 与主浏览器完全隔离，可多账号同时在线互不干扰。"
    "默认以 100% 全屏打开窗口；可选传入 account（accounts.json 账号名）直接登录。"
    "窗口归属本服务管理，disconnect 时关闭；不影响主浏览器窗口。",
    icons=[_BROWSER_ICON],
    tags={"browser", "session", "isolation"},
)
async def session_open_isolated(
    ctx: Context,
    name: str,
    account: str = "",
    fullscreen: bool = True,
    username: str | None = None,
    password: str | None = None,
) -> dict:
    """打开隔离会话（无痕窗口），可选登录账号。

    Args:
        name: 会话名。
        account: accounts.json 账号名（提供则直接登录）。
        fullscreen: 是否 100% 全屏打开窗口（默认 true）。
        username/password: 显式凭据（account 为空时使用）。
    """
    from qa_automation.browser import accounts
    from qa_automation.browser.login import DEFAULT_SCM_BASE_URL, api_login_and_inject
    from qa_automation.browser.vision import VisionCaptchaRecognizer

    lc = _lifecycle(ctx)
    try:
        session = await lc.create_session(
            name, use_default=False, account=account or None
        )
    except Exception as exc:
        return _err(exc)
    await ctx.info(f"isolated window opened: {name} (cookie-isolated context)")

    if fullscreen:
        try:
            await lc.set_session_window_state(name, "fullscreen")
        except Exception as exc:
            await lc.close_session(name)
            return {
                "ok": False,
                "error": f"session created but fullscreen failed: {type(exc).__name__}: {exc}",
            }

    logged_in = False
    if account or (username and password):
        cfg = accounts.load_accounts()
        base_url = (
            cfg.get("base_url")
            or os.environ.get("SCM_BASE_URL", DEFAULT_SCM_BASE_URL)
        ).rstrip("/")
        cred = accounts.resolve(account, username, password)
        if cred is None:
            return {
                "ok": True,
                "name": name,
                "isolated": True,
                "fullscreen": fullscreen,
                "warning": f"account {account!r} not found; window opened without login",
            }
        try:
            cookies = await api_login_and_inject(
                lc, name, base_url, cred[0], cred[1], VisionCaptchaRecognizer()
            )
            logged_in = True
        except Exception as exc:
            return {
                "ok": True,
                "name": name,
                "isolated": True,
                "fullscreen": fullscreen,
                "warning": f"window opened but login failed: {type(exc).__name__}: {exc}",
            }

    return {
        "ok": True,
        "name": name,
        "isolated": True,
        "fullscreen": fullscreen,
        "logged_in": logged_in,
        "account": account or None,
    }


@tool(
    title="Session: Create",
    description="新建命名会话。默认在浏览器默认上下文（你已打开的窗口）中操作，登录/导航可见，"
    "该会话受保护不可关闭；use_default=false 时创建全新空 cookie 的隔离会话（多账号测试）。"
    "storage_state 可恢复已保存账号态（两种模式均支持）。",
    icons=[_BROWSER_ICON],
    tags={"browser", "session"},
)
async def session_create(
    ctx: Context,
    name: str,
    storage_state: str | None = None,
    use_default: bool = True,
    account: str | None = None,
) -> dict:
    """创建会话并设为激活。

    Args:
        name: 会话名（WMS/APS 多账号场景建议按角色命名，如 admin、warehouse）。
        storage_state: 账号态文件路径（session_save_state 的产物）。
        use_default: true（默认）=使用浏览器默认上下文（你可见的窗口，受保护不可关闭）;
            false=新建隔离会话（全新空 cookie，多账号会话分离）。
        account: 绑定的账号名（多账号路由防串窗；session_list 可查映射）。
    """
    lc = _lifecycle(ctx)
    try:
        session = await lc.create_session(
            name,
            storage_state=storage_state,
            use_default=use_default,
            account=account,
        )
    except Exception as exc:
        return _err(exc)
    await ctx.info(
        f"session {name!r} created ({'default context' if use_default else 'fresh cookie jar'})"
    )
    return {"ok": True, "name": name, "active": True, "default": not session.managed}


@tool(
    title="Session: Switch",
    description="切换激活会话，后续页面操作缺省作用于它。",
    icons=[_BROWSER_ICON],
    tags={"browser", "session"},
)
async def session_switch(ctx: Context, name: str) -> dict:
    """切换激活会话。

    Args:
        name: 目标会话名。
    """
    try:
        await _lifecycle(ctx).switch_session(name)
    except Exception as exc:
        return _err(exc)
    return {"ok": True, "active": name}


@tool(
    title="Session: List",
    description="列出全部会话。",
    icons=[_BROWSER_ICON],
    tags={"browser", "session"},
)
async def session_list(ctx: Context) -> dict:
    """列出会话及其页面数。"""
    return {"ok": True, "sessions": _lifecycle(ctx).sessions()}


@tool(
    title="Session: Close",
    description="关闭指定会话及其页面。",
    icons=[_BROWSER_ICON],
    tags={"browser", "session"},
)
async def session_close(ctx: Context, name: str) -> dict:
    """关闭会话。

    Args:
        name: 会话名。
    """
    try:
        await _lifecycle(ctx).close_session(name)
    except Exception as exc:
        return _err(exc)
    return {"ok": True, "closed": name}


@tool(
    title="Session: Save State",
    description="将会话账号态（cookie/localStorage）落盘，供 session_create 恢复。默认存 .auth/<name>.json。",
    icons=[_BROWSER_ICON],
    tags={"browser", "session", "auth"},
)
async def session_save_state(ctx: Context, name: str, path: str | None = None) -> dict:
    """保存账号态。

    Args:
        name: 会话名。
        path: 目标文件；缺省 .auth/<name>.json。
    """
    lc = _lifecycle(ctx)
    dest = path or str(AUTH_DIR / f"{name}.json")
    try:
        saved = await lc.save_storage_state(name, dest)
    except Exception as exc:
        return _err(exc)
    await ctx.info(f"session {name!r} state saved to {saved}")
    return {"ok": True, "path": saved}


@tool(
    title="Page: Goto",
    description="在会话中打开 URL。",
    icons=[_BROWSER_ICON],
    tags={"browser", "page"},
)
async def page_goto(
    ctx: Context,
    url: str,
    session: str | None = None,
    wait_until: str = "load",
) -> dict:
    """导航到 URL。

    Args:
        url: 目标地址。
        session: 目标会话名（多账号场景必须显式指定，防止操作落到其他账号窗口；可先 session_list 确认映射）；缺省使用激活会话。
        wait_until: load|domcontentloaded|networkidle|commit。
    """
    lc = _lifecycle(ctx)
    try:
        page = await lc.page(session)
        await page.goto(url, wait_until=wait_until)
        return {"ok": True, "url": page.url, "title": await page.title()}
    except Exception as exc:
        return _err(exc)


@tool(
    title="Page: Title",
    description="读取会话当前页标题与 URL。",
    icons=[_BROWSER_ICON],
    tags={"browser", "page"},
)
async def page_title(ctx: Context, session: str | None = None) -> dict:
    """读取页面标题。

    Args:
        session: 目标会话名（多账号场景必须显式指定，防止操作落到其他账号窗口；可先 session_list 确认映射）；缺省使用激活会话。
    """
    lc = _lifecycle(ctx)
    try:
        page = await lc.page(session)
        return {"ok": True, "title": await page.title(), "url": page.url}
    except Exception as exc:
        return _err(exc)


@tool(
    title="Tab: List",
    description="列出会话上下文中的所有标签页（URL/标题/是否前台激活），用于确认接管哪个业务页面。",
    icons=[_BROWSER_ICON],
    tags={"browser", "tab"},
)
async def tab_list(ctx: Context, session: str | None = None) -> dict:
    """列出会话的标签页。

    Args:
        session: 目标会话名（多账号场景必须显式指定；缺省使用激活会话）。
    """
    lc = _lifecycle(ctx)
    try:
        tabs = await lc.list_tabs(session)
        return {"ok": True, "tabs": tabs}
    except Exception as exc:
        return _err(exc)


@tool(
    title="Tab: Switch",
    description="将会话切换到指定标签页并前台激活（接管已打开的业务页面，不新建标签页）。"
    "可用 index（tab_list 的序号）或 url_contains（URL 子串）定位。",
    icons=[_BROWSER_ICON],
    tags={"browser", "tab"},
)
async def tab_switch(
    ctx: Context,
    session: str | None = None,
    index: int | None = None,
    url_contains: str | None = None,
) -> dict:
    """切换会话到指定标签页。

    Args:
        session: 目标会话名（多账号场景必须显式指定；缺省使用激活会话）。
        index: 标签页序号（tab_list 的 index）。
        url_contains: URL 子串匹配（优先于 index）。
    """
    lc = _lifecycle(ctx)
    try:
        page = await lc.switch_tab(session, index=index, url_contains=url_contains)
        return {"ok": True, "url": page.url, "title": await page.title()}
    except Exception as exc:
        return _err(exc)


@tool(
    title="Page: Interact (通用交互)",
    description="通用元素交互：支持语义定位（role/name、text、placeholder）、xpath、css 与视口坐标点击。"
    "定位信息优先取自 analyze_current_page 输出（gbr/css/xpath/x/y）。坐标模式不做任何坐标计算，"
    "直接使用传入的 x/y（视口绝对坐标）。in_iframe 控制是否在激活 iframe 内查找（默认 true）。"
    "select 动作自动识别 Ant Design 下拉（.ant-select）：点击展开→按下拉选项文本选中；"
    "原生 <select> 走 Playwright select_option。",
    icons=[_BROWSER_ICON],
    tags={"browser", "page", "qa"},
)
async def page_interact(
    ctx: Context,
    session: str | None = None,
    action: str = "click",
    role: str | None = None,
    name: str | None = None,
    text: str | None = None,
    placeholder: str | None = None,
    css: str | None = None,
    xpath: str | None = None,
    x: float | None = None,
    y: float | None = None,
    value: str | None = None,
    input_method: str = "fill",
    clear_first: bool = True,
    press_enter: bool = False,
    in_iframe: bool = True,
    visualize: bool | None = None,
    timeout_ms: int = 30_000,
) -> dict:
    """通用交互（一次调用完成定位+动作）。

    Args:
        session: 目标会话名（多账号场景必须显式指定；缺省使用激活会话）。
        action: click/fill/hover/dblclick/rightclick/select/press/check/uncheck。
        role/name: 语义定位（get_by_role；name 取 analyze_current_page 返回的真实值，如"新 增"）。
        text: 按可见文本定位。
        placeholder: 按占位符定位。
        css/xpath: 结构定位（xpath 用 analyze 返回的 xpath）。
        x/y: 视口绝对坐标（坐标模式，与定位参数互斥；工具不做坐标计算，直接使用）。
        value: fill/select/press 的输入值。
        input_method: fill=Playwright 原生填充（快稳，自动清空）; type=逐字模拟打字
            （Ctrl+A 清空 + 0.1s 间隔，适用于监听键盘事件的组件）。
        clear_first: type 模式输入前清空已有内容（fill 天然清空）。
        press_enter: 输入完成后按回车（搜索框/确认输入场景）。
        in_iframe: 是否在激活 iframe 内查找（默认 true；false 仅搜顶层）。
        visualize: 是否显示虚拟光标（移动/高亮/点击波纹）。缺省读 .env VISUAL_CURSOR_ENABLED。
        timeout_ms: 等待超时（毫秒）。
    """
    from qa_automation.browser.visual import VirtualCursor

    visualize = _visualize_default(visualize)
    lc = _lifecycle(ctx)
    try:
        page = await lc.page(session)
    except Exception as exc:
        return _err(exc)

    if visualize:
        try:
            await VirtualCursor.attach(page)
        except Exception:  # noqa: BLE001 - 特效注入失败不影响交互
            visualize = False

    async def _clear_visuals():
        if visualize:
            try:
                await VirtualCursor.clear(page)
            except Exception:  # noqa: BLE001
                pass

    # 坐标模式：不做坐标计算，直接使用
    if x is not None or y is not None:
        if x is None or y is None:
            return {"ok": False, "error": "coordinate mode requires both x and y"}
        try:
            if visualize and action in ("click", "dblclick", "rightclick"):
                await VirtualCursor.click_at(page, x, y)
            elif visualize:
                await VirtualCursor.move_to(page, x, y)
            if action in ("click", "dblclick"):
                fn = page.mouse.dblclick if action == "dblclick" else page.mouse.click
                await fn(x, y)
            elif action == "hover":
                await page.mouse.move(x, y)
            elif action == "rightclick":
                await page.mouse.click(x, y, button="right")
            else:
                return {"ok": False, "error": f"action {action!r} not supported in coordinate mode"}
            if visualize and action in ("click", "dblclick", "rightclick"):
                await asyncio.sleep(0.65)  # 点击波纹可见后再消失（clear 在 finally）
            observation = await _observe_layers(page) if action in ("click", "dblclick", "rightclick", "hover") else None
            return {"ok": True, "mode": "coordinate", "x": x, "y": y, "action": action, "visualize": visualize, "observation": observation}
        except Exception as exc:
            return _err(exc)
        finally:
            await _clear_visuals()

    # 定位模式
    try:
        locator = await _resolve_locator(
            page,
            role=role,
            name=name,
            text=text,
            placeholder=placeholder,
            css=css,
            xpath=xpath,
            in_iframe=in_iframe,
        )
        if action == "click":
            box = await locator.bounding_box()
            if visualize and box is not None:
                # 目标高亮（呼吸框）+ 光标移动到元素中心
                await VirtualCursor.target(
                    page, box["x"], box["y"], box["width"], box["height"]
                )
            try:
                # 快速尝试：元素 5s 内出现且可交互则正常点击；否则降级坐标兜底
                await locator.click(timeout=min(timeout_ms, 5000))
            except Exception:
                # actionability 检查超时（遮挡/动画等）→ 元素中心坐标物理点击兜底
                if box is None:
                    box = await locator.bounding_box()
                if box is None:
                    raise
                await page.mouse.click(
                    box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                )
                if visualize:
                    await VirtualCursor.click_at(
                        page, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                    )
                    await asyncio.sleep(0.65)  # 点击波纹可见后再消失
                observation = await _observe_layers(page)
                return {
                    "ok": True,
                    "mode": "locator",
                    "action": action,
                    "fallback": "coordinate-center",
                    "observation": observation,
                    "locator": {
                        "role": role, "name": name, "text": text,
                        "placeholder": placeholder, "css": css, "xpath": xpath,
                    },
                }
        elif action == "dblclick":
            await locator.dblclick(timeout=timeout_ms)
        elif action == "rightclick":
            await locator.click(button="right", timeout=timeout_ms)
        elif action == "hover":
            await locator.hover(timeout=timeout_ms)
        elif action == "fill":
            if value is None:
                return {"ok": False, "error": "fill requires value"}
            await _do_fill_with_visual(
                page,
                locator,
                value,
                input_method=input_method,
                clear_first=clear_first,
                press_enter=press_enter,
                visualize=visualize,
            )
        elif action == "select":
            if value is None:
                return {"ok": False, "error": "select requires value"}
            # antd Select（div 组合，select_option 无效）→ 一步展开+选 option；
            # 原生 <select> 保持 Playwright select_option。
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
            is_antd = await locator.evaluate(
                "el => !!el.closest('.ant-select, .ant-cascader, .ant-tree-select')"
            )
            if is_antd:
                await _antd_select_option(page, frame, locator, value)
            else:
                await locator.select_option(value)
        elif action == "press":
            if value is None:
                return {"ok": False, "error": "press requires value"}
            await locator.press(value)
        elif action == "check":
            await locator.check()
        elif action == "uncheck":
            await locator.uncheck()
        else:
            return {"ok": False, "error": f"unknown action {action!r}"}
        # 点击类动作：点击反馈波纹（视觉可见后再随 finally clear 消失）
        if visualize and action in ("click", "dblclick", "rightclick"):
            box = await locator.bounding_box()
            if box is not None:
                try:
                    await VirtualCursor.click_at(
                        page,
                        box["x"] + box["width"] / 2,
                        box["y"] + box["height"] / 2,
                    )
                    await asyncio.sleep(0.65)
                except Exception:  # noqa: BLE001 - 波纹失败不影响结果
                    pass
        observation = (
            await _observe_layers(page)
            if action in ("click", "dblclick", "rightclick", "hover", "fill", "select", "press")
            else None
        )
        return {
            "ok": True,
            "mode": "locator",
            "action": action,
            "observation": observation,
            "visualize": visualize,
            "locator": {
                "role": role, "name": name, "text": text,
                "placeholder": placeholder, "css": css, "xpath": xpath,
            },
        }
    except Exception as exc:
        return _err(exc)
    finally:
        await _clear_visuals()


@tool(
    title="Page: Click",
    description="点击元素。语义定位优先（AI 推荐）：role+name（如 role=button name=查询）或 text（可见文本）；"
    "css 选择器仅作结构兜底。",
    icons=[_BROWSER_ICON],
    tags={"browser", "page", "qa"},
)
async def page_click(
    ctx: Context,
    session: str | None = None,
    role: str | None = None,
    name: str | None = None,
    text: str | None = None,
    css: str | None = None,
    timeout_ms: int = 30_000,
) -> dict:
    """点击元素。

    Args:
        session: 目标会话名（多账号场景必须显式指定，防止操作落到其他账号窗口；可先 session_list 确认映射）；缺省使用激活会话。
        role: 无障碍角色（button/textbox/combobox/link/checkbox/radio...）。
        name: 可访问名（与 role 配合，如 role=button name=查询；子串匹配）。
        text: 按可见文本定位。
        css: CSS 选择器（结构兜底，优先用语义定位）。
        timeout_ms: 等待超时（毫秒）。
    """
    lc = _lifecycle(ctx)
    try:
        page = await lc.page(session)
        locator = await _resolve_locator(
            page, role=role, name=name, text=text, css=css
        )
        await locator.click(timeout=timeout_ms)
        return {"ok": True, "locator": {"role": role, "name": name, "text": text, "css": css}}
    except Exception as exc:
        return _err(exc)


@tool(
    title="Page: Fill",
    description="填充输入框。语义定位优先（AI 推荐）：role=textbox+name 或 placeholder；css 选择器仅作结构兜底。"
    "支持 fill（原生填充，快稳）/ type（逐字模拟打字）双输入模式、输入后回车（press_enter）与光标可视化。",
    icons=[_BROWSER_ICON],
    tags={"browser", "page", "qa"},
)
async def page_fill(
    ctx: Context,
    value: str,
    session: str | None = None,
    role: str | None = None,
    name: str | None = None,
    placeholder: str | None = None,
    css: str | None = None,
    input_method: str = "type",
    clear_first: bool = True,
    press_enter: bool = False,
    visualize: bool | None = None,
) -> dict:
    """填充输入框。

    Args:
        value: 输入值。
        session: 目标会话名（多账号场景必须显式指定，防止操作落到其他账号窗口；可先 session_list 确认映射）；缺省使用激活会话。
        role: 输入框角色（默认 textbox）。
        name: 可访问名（与 role 配合）。
        placeholder: 按占位符定位（如 placeholder=请输入单号）。
        css: CSS 选择器（结构兜底，优先用语义定位）。
        input_method: type=逐字模拟打字（默认，Ctrl+A 清空 + 0.1s 间隔，打字机效果，
            适用于监听键盘事件的组件）; fill=Playwright 原生填充（快稳，自动清空）。
        clear_first: type 模式输入前清空已有内容（fill 天然清空）。
        press_enter: 输入完成后按回车（搜索框/确认输入场景）。
        visualize: 是否显示虚拟光标移动到输入框 + 高亮呼吸框。缺省读 .env VISUAL_CURSOR_ENABLED。
    """
    visualize = _visualize_default(visualize)
    lc = _lifecycle(ctx)
    page = None
    try:
        page = await lc.page(session)
        # 默认 role=textbox 仅在没有 placeholder/css 等结构维度时生效
        locator = await _resolve_locator(
            page,
            role=None if (placeholder or css) else (role or "textbox"),
            name=name,
            placeholder=placeholder,
            css=css,
        )
        await _do_fill_with_visual(
            page,
            locator,
            value,
            input_method=input_method,
            clear_first=clear_first,
            press_enter=press_enter,
            visualize=visualize,
        )
        observation = await _observe_layers(page)
        return {
            "ok": True,
            "input_method": input_method,
            "clear_first": clear_first,
            "press_enter": press_enter,
            "observation": observation,
            "locator": {"role": role or "textbox", "name": name, "placeholder": placeholder, "css": css},
        }
    except Exception as exc:
        return _err(exc)
    finally:
        if visualize and page is not None:
            try:
                from qa_automation.browser.visual import VirtualCursor

                await VirtualCursor.clear(page)
            except Exception:  # noqa: BLE001
                pass


@tool(
    title="Page: Screenshot",
    description="页面截图保存到 artifacts/screenshots/ 并返回路径。",
    icons=[_BROWSER_ICON],
    tags={"browser", "page", "qa"},
)
async def page_screenshot(
    ctx: Context,
    session: str | None = None,
    path: str | None = None,
    full_page: bool = False,
) -> dict:
    """页面截图。

    Args:
        session: 目标会话名（多账号场景必须显式指定，防止操作落到其他账号窗口；可先 session_list 确认映射）；缺省使用激活会话。
        path: 保存路径；缺省 artifacts/screenshots/ 时间戳命名。
        full_page: 是否整页截图。
    """
    lc = _lifecycle(ctx)
    try:
        page = await lc.page(session)
        dest = Path(path) if path else SCREENSHOT_DIR / f"shot_{int(time.time() * 1000)}.png"
        dest.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(dest), full_page=full_page)
        return {"ok": True, "path": str(dest)}
    except Exception as exc:
        return _err(exc)

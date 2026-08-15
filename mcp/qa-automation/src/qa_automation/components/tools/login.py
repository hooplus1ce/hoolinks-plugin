"""账号密码快速登录 MCP 工具（API 直登 + 多模态视觉识别）。

流程：直接请求验证码 URL → 视觉模型（gemini-3.6-flash, low 思考）识别 →
登录请求 → 登录态 cookies 注入当前会话上下文 → goto 目标待测页。
不做页面渲染截图；验证码识别失败自动重试（刷新验证码）。
"""
from __future__ import annotations

import os

from fastmcp import Context
from fastmcp.tools import tool
from mcp.types import Icon

from qa_automation.browser.login import DEFAULT_SCM_BASE_URL, api_login_and_inject
from qa_automation.browser.vision import VisionCaptchaRecognizer

_LOGIN_ICON = Icon(
    src="data:image/svg+xml;base64,PHN2ZyB4bWxucz0naHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmcnIHdpZHRoPScyNCcgaGVpZ2h0PScyNCc+PHJlY3QgeD0nNCcgeT0nOScgd2lkdGg9JzE2JyBoZWlnaHQ9JzEwJyByeD0nMicgZmlsbD0nbm9uZScgc3Ryb2tlPSclMjMxZjdhZTQnIHN0cm9rZS13aWR0aD0nMicvPjxwYXRoIGQ9J00xMCAyMHYtNW0wLTJ2LTNhNiA2IDAgMCAxIDEyIDAnIGZpbGw9J25vbmUnIHN0cm9rZT0nJTIzMWY3YWU0JyBzdHJva2Utd2lkdGg9JzInLz48Y2lyY2xlIGN4PScxNicgY3k9JzE1JyByPScxJyBmaWxsPSclMjMxZjdhZTQnLz48L3N2Zz4=",
    mime_type="image/svg+xml",
)

# 验证码场景思考强度固定 low（加快识别速度，验证码无需深度推理）
_CAPTCHA_THINKING_LEVEL = "low"

_RECOGNIZER: VisionCaptchaRecognizer | None = None


def _recognizer() -> VisionCaptchaRecognizer:
    global _RECOGNIZER
    if _RECOGNIZER is None:
        _RECOGNIZER = VisionCaptchaRecognizer(thinking_level=_CAPTCHA_THINKING_LEVEL)
    return _RECOGNIZER


@tool(
    title="Login: Captcha (SCM)",
    description="SCM 完整登录（API 直登，直接调用本工具即可，无需拆分步骤）：自动请求验证码接口 → 多模态视觉"
    "识别（gemini-3.6-flash, low 思考）→ 登录请求 → 登录态 cookies 注入当前会话 → 跳转工作台。"
    "账号密码按 account 名从 accounts.json 自动读取（account_add 管理），密码不经过对话。",
    icons=[_LOGIN_ICON],
    tags={"login", "auth", "session"},
)
async def login_with_captcha(
    ctx: Context,
    session: str,
    account: str = "",
    username: str | None = None,
    password: str | None = None,
    base_url: str | None = None,
) -> dict:
    """SCM API 直登（视觉识别验证码）。

    Args:
        session: 目标会话名（先 session_create；cookies 注入该会话上下文）。
        account: accounts.json 中的账号名（如 admin/operator）；为空时回退
            username/password 显式参数或环境变量 SCM_USERNAME/SCM_USERPWD。
        username: 账号；缺省按 account 读取。
        password: 密码；缺省按 account 读取。
        base_url: 系统地址；缺省读 accounts.json 的 base_url 或 SCM_BASE_URL。
    """
    from qa_automation.browser import accounts

    cfg = accounts.load_accounts()
    base_url = (
        base_url
        or cfg.get("base_url")
        or os.environ.get("SCM_BASE_URL", DEFAULT_SCM_BASE_URL)
    ).rstrip("/")
    cred = accounts.resolve(account, username, password)
    if cred is None:
        if account:
            return {
                "ok": False,
                "error": f"account {account!r} not found in accounts.json "
                "(use account_add to configure)",
            }
        return {
            "ok": False,
            "error": "missing credentials: pass account name (configured via account_add) "
            "or username/password",
        }
    username, password = cred
    if not base_url:
        return {"ok": False, "error": "missing base_url (accounts.json or SCM_BASE_URL)"}

    lc = ctx.lifespan_context["lifecycle"]
    try:
        cookies = await api_login_and_inject(
            lc, session, base_url, username, password, _recognizer()
        )
    except Exception as exc:
        return {"ok": False, "error": f"login failed: {type(exc).__name__}: {exc}"}

    await ctx.info(
        f"session {session!r} logged in via API+vision; {len(cookies)} cookies injected"
    )
    return {
        "ok": True,
        "session": session,
        "account": account or username,
        "cookies": len(cookies),
        "method": "api-vision",
        "workbench": f"{base_url}/static/admin",
    }

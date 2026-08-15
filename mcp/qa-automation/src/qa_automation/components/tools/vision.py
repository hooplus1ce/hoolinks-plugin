"""多模态视觉识别 MCP 工具（Antigravity gemini-3.6-flash，替代 OCR 方案）。

- vision_recognize: 通用视觉识别（本地图片路径/URL → 模型文本）。
- captcha_recognize: 对会话页面验证码元素局部截图（Playwright）→ 视觉识别 → 验证码数字。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from fastmcp import Context
from fastmcp.tools import tool
from mcp.types import Icon

from qa_automation.browser import vision as v
from qa_automation.browser.lifecycle import PlaywrightLifecycle

_VISION_ICON = Icon(
    src="data:image/svg+xml;base64,PHN2ZyB4bWxucz0naHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmcnIHdpZHRoPScyNCcgaGVpZ2h0PScyNCc+PHJlY3QgeD0nMycgeT0nNScgd2lkdGg9JzE4JyBoZWlnaHQ9JzE0JyByeD0nMicgZmlsbD0nbm9uZScgc3Ryb2tlPSclMjM1YzY3YzgnIHN0cm9rZS13aWR0aD0nMicvPjxjaXJjbGUgY3g9JzEyJyBjeT0nMTInIHI9JzMnIGZpbGw9JyUyMzVjNjdjOCcvPjxwYXRoIGQ9J00zIDlsMy0yaDVsMyAyaDd2MTBIM3onIGZpbGw9J25vbmUnIHN0cm9rZT0nJTIzNWM2N2M4JyBzdHJva2Utd2lkdGg9JzInLz48L3N2Zz4=",
    mime_type="image/svg+xml",
)

# 视觉验证码识别器全局单例（模型每次调用按图发请求，无本地状态）
_RECOGNIZER: v.VisionCaptchaRecognizer | None = None


def _recognizer() -> v.VisionCaptchaRecognizer:
    global _RECOGNIZER
    if _RECOGNIZER is None:
        _RECOGNIZER = v.VisionCaptchaRecognizer()
    return _RECOGNIZER


def _lifecycle(ctx: Context) -> PlaywrightLifecycle:
    return ctx.lifespan_context["lifecycle"]


@tool(
    title="Vision: Recognize",
    description="多模态视觉识别：本地图片路径或 URL → gemini-3.6-flash（Antigravity）识别并返回文本。"
    "纯文本主模型的视觉能力补充（vision role）。",
    icons=[_VISION_ICON],
    tags={"vision"},
)
async def vision_recognize(
    ctx: Context,
    image: str,
    question: str = "请描述图片中的内容",
    thinking_level: str = "low",
) -> dict:
    """通用视觉识别。

    Args:
        image: 本地图片路径或 http(s) 图片 URL。
        question: 识别要求（默认描述图片内容）。
        thinking_level: minimal/low/medium/high（思考深度）。
    """
    try:
        if image.startswith(("http://", "https://")):
            resp = await asyncio.to_thread(httpx.get, image, timeout=30.0)
            resp.raise_for_status()
            data = resp.content
        else:
            data = Path(image).read_bytes()
        text, cached = await asyncio.to_thread(
            v.recognize_image_cached, data, question, thinking_level=thinking_level
        )
        return {"ok": True, "text": text, "cached": cached}
    except Exception as exc:  # noqa: BLE001 - 工具返回结构化错误
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@tool(
    title="Vision: Antigravity Login",
    description="Antigravity 视觉通道 OAuth 授权登录（对话内完成）：打开 Google 授权网页，等待用户完成授权"
    "（180s 超时）后持久化凭据。凭据失效/报错时调用；已登录时直接返回成功（幂等）。",
    icons=[_VISION_ICON],
    tags={"vision", "auth"},
)
async def vision_login(ctx: Context) -> dict:
    """Antigravity 授权登录（凭据已有效则跳过）。"""
    from qa_automation.browser import vision as v

    try:
        creds = v.ensure_valid_credentials()
        return {
            "ok": True,
            "already_logged_in": True,
            "project_id": creds.project_id,
            "credentials": str(v.credentials_path()),
        }
    except RuntimeError:
        pass  # 未登录/已失效 → 走完整授权流

    try:
        creds = await asyncio.to_thread(v.login, False)  # 工具场景：超时不等待粘贴
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    await ctx.info(f"Antigravity authorized: projectId={creds.project_id}")
    return {
        "ok": True,
        "already_logged_in": False,
        "project_id": creds.project_id,
        "credentials": str(v.credentials_path()),
    }


@tool(
    title="Vision: Captcha Recognize",
    description="直接请求验证码接口获取图片数据并视觉识别（gemini-3.6-flash, low 思考），"
    "返回验证码数字。不依赖页面渲染/截图。",
    icons=[_VISION_ICON],
    tags={"vision", "login"},
)
async def captcha_recognize(
    ctx: Context,
    url: str | None = None,
    base_url: str | None = None,
) -> dict:
    """识别验证码（直接请求验证码接口）。

    Args:
        url: 完整验证码图片 URL；缺省按 base_url 拼接 SCM 验证码接口。
        base_url: 系统根地址；缺省读 SCM_BASE_URL。
    """
    import os
    import time as _time

    from qa_automation.browser.login import DEFAULT_SCM_BASE_URL, SCM_LOGIN_CONFIG

    base = (base_url or os.environ.get("SCM_BASE_URL", DEFAULT_SCM_BASE_URL)).rstrip("/")
    if url is None:
        url = (
            f"{base}{SCM_LOGIN_CONFIG['validate_code_path']}"
            f"?key={SCM_LOGIN_CONFIG['validate_code_key']}&random={_time.time():.6f}"
        )
    try:
        resp = await asyncio.to_thread(
            httpx.get,
            url,
            timeout=30.0,
            headers={"Referer": f"{base}/", "Origin": base},
        )
        resp.raise_for_status()
        code = await asyncio.to_thread(_recognizer().classification, resp.content)
        if not code:
            return {"ok": False, "error": "视觉识别未得到有效验证码（接口可能限流或图片异常）"}
        return {"ok": True, "captcha": code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

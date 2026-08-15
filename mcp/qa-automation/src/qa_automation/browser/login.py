"""登录核心：验证码登录（视觉识别 + 页面驱动/API 两种模式）。

识别方式（替代原 ddddocr OCR 方案）：多模态视觉模型 gemini-3.6-flash
（browser/vision.py），识别器统一接口 classification(image_bytes) -> str。

两种登录模式：
- page_login：页面驱动（推荐）——Playwright 打开登录页，对验证码元素局部
  截图 → 视觉识别 → 填表提交，全程在用户可见窗口进行，登录态由前端原生写入；
- captcha_login：API 直登——httpx 下载验证码 → 视觉识别 → JSON 提交，
  token 提取自响应体（HL-Access-Token）。
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any
from urllib.parse import urlparse

import httpx

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)

# 验证码请求头（同源浏览器语义，取自原方案）
_CAPTCHA_HEADERS = {
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Pragma": "no-cache",
    "Sec-Fetch-Dest": "image",
    "Sec-Fetch-Mode": "no-cors",
    "Sec-Fetch-Site": "same-origin",
    "sec-ch-ua": '"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}


class LoginError(RuntimeError):
    """登录失败：HTTP 错误或服务端显式拒绝。"""


# demo18-scm.hoolinks.com（WMS 管理平台）登录接口实测参数（API 直登模式）
SCM_LOGIN_CONFIG: dict[str, Any] = {
    "validate_code_path": "/scmpsm/login/validateCode",
    "signin_path": "/scmpsm/login/signin",
    "validate_code_key": "regValidateCode",
    "username_field": "userName",
    "password_field": "userPwd",
    "vcode_field": "vcode",
    "json_body": True,
    "referer": "/static/admin/login",
    # token 在响应体 data 字段，前端 JS 手动写入 HL-Access-Token cookie
    "response_token_path": "data",
    "token_cookie_name": "HL-Access-Token",
}

from qa_automation.config import SCM_BASE_URL as DEFAULT_SCM_BASE_URL


async def api_login_and_inject(
    lc,
    session_name: str,
    base_url: str,
    username: str,
    password: str,
    recognizer: Any,
    *,
    workbench_path: str = "/static/admin",
) -> list[dict]:
    """API 直登（推荐）：下载验证码 → 视觉识别 → 登录请求 → cookies 注入会话 → goto 目标页。

    不做页面渲染截图；登录态 cookies（含响应体 token 合成的 HL-Access-Token）
    直接注入当前会话上下文，随后导航到工作台。验证码识别失败自动重试（3 次）。

    Returns:
        注入的 cookie 列表。

    Raises:
        LoginError: 验证码识别失败或登录被拒（3 次尝试后）。
    """
    from qa_automation.browser.lifecycle import PlaywrightLifecycle  # noqa: PLC0415 - 避免循环导入

    lc: PlaywrightLifecycle = lc
    base_url = base_url.rstrip("/")
    last_error: BaseException | None = None
    for _ in range(3):
        try:
            cookies = await captcha_login(
                base_url,
                username,
                password,
                recognizer,
                **SCM_LOGIN_CONFIG,
                validate_code_extra_params={"random": f"{time.time():.6f}"},
            )
            break
        except LoginError as exc:
            last_error = exc
            await asyncio.sleep(1)
    else:
        raise LoginError(f"登录失败（3 次尝试）: {last_error}") from last_error

    context = lc.context(session_name)
    await context.add_cookies(cookies)
    page = await lc.page(session_name)
    await page.goto(f"{base_url}{workbench_path}")
    return cookies


async def captcha_login(
    base_url: str,
    username: str,
    password: str,
    ocr: Any,
    *,
    validate_code_path: str = "/validateCode.json",
    signin_path: str = "/signin.html",
    validate_code_key: str = "regValidateCode",
    validate_code_extra_params: dict[str, Any] | None = None,
    username_field: str = "username",
    password_field: str = "userpwd",
    vcode_field: str = "vcode",
    json_body: bool = False,
    referer: str | None = None,
    response_token_path: str | None = None,
    token_cookie_name: str | None = None,
    ua: str = DEFAULT_UA,
) -> list[dict]:
    """执行验证码登录，返回可注入 Playwright 上下文的 cookie 列表。

    Args:
        base_url: 系统根地址（如 https://demo18-scm.hoolinks.com）。
        username: 登录账号。
        password: 登录密码。
        ocr: 具备 classification(image_bytes) -> str 的识别器（见 OcrHolder）。
        validate_code_path: 验证码图片接口。
        signin_path: 登录提交接口。
        validate_code_key: 验证码接口的查询参数 key。
        validate_code_extra_params: 附加查询参数（如防缓存 random）。
        username_field: 登录请求的账号字段名。
        password_field: 登录请求的密码字段名。
        vcode_field: 登录请求的验证码字段名。
        json_body: 登录请求体为 JSON（True）或表单（False）。
        referer: 请求 Referer 路径；缺省 base_url + "/"。
        response_token_path: 登录响应 JSON 中 token 的点路径（如 "data"）；
            部分系统 token 在响应体而非 Set-Cookie，前端自行写入 cookie。
        token_cookie_name: 提取的 token 要写入的 cookie 名（如 HL-Access-Token）。
        ua: 请求 User-Agent。

    Raises:
        LoginError: 登录 HTTP 失败或服务端响应显式拒绝（ok=false 或 json code 非 0）。
    """
    base_url = base_url.rstrip("/")
    host = urlparse(base_url).hostname or "127.0.0.1"
    headers = {
        **_CAPTCHA_HEADERS,
        "User-Agent": ua,
        "Referer": f"{base_url}{referer}" if referer else f"{base_url}/",
        "Origin": base_url,
    }

    # 1+2. 预置 SESSION，获取验证码
    jar = httpx.Cookies()
    jar.set("SESSION", str(uuid.uuid4()), domain=host, path="/")
    params: dict[str, Any] = {"key": validate_code_key}
    if validate_code_extra_params:
        params.update(validate_code_extra_params)
    async with httpx.AsyncClient(base_url=base_url, cookies=jar, follow_redirects=True) as client:
        resp = await client.get(validate_code_path, params=params, headers=headers)
        resp.raise_for_status()
        code = str(ocr.classification(resp.content)).strip()

        # 3. 提交登录（同一 cookie jar）
        body = {
            username_field: username,
            password_field: password,
            vcode_field: code,
        }
        request_kwargs = {"json": body} if json_body else {"data": body}
        resp = await client.post(
            signin_path, headers={**headers, "Accept": "*/*"}, **request_kwargs
        )
        if resp.status_code >= 400:
            raise LoginError(f"signin HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            payload = resp.json()
        except Exception:
            payload = None
        if isinstance(payload, dict):
            if "ok" in payload and not payload["ok"]:
                raise LoginError(f"signin rejected by server: {payload}")
            if "code" in payload and payload["code"] not in (0, "0", 200):
                raise LoginError(f"signin rejected by server: {payload}")
            token: Any = payload
            if response_token_path:
                for part in response_token_path.split("."):
                    token = token.get(part) if isinstance(token, dict) else None
                    if token is None:
                        break

    # 4. 完整 cookie jar（SESSION + 登录态）转 Playwright 注入格式；
    #    响应体 token（前端手动写入的 cookie）补充注入。
    cookies = _to_pw_cookies(client.cookies)
    if response_token_path and token_cookie_name and isinstance(token, str) and token:
        cookies.append(
            {
                "name": token_cookie_name,
                "value": token,
                "domain": host,
                "path": "/",
                "secure": False,
                "httpOnly": False,
            }
        )
    return cookies


def _to_pw_cookies(cookies: httpx.Cookies) -> list[dict]:
    out: list[dict] = []
    for c in cookies.jar:
        out.append(
            {
                "name": c.name,
                "value": c.value,
                "domain": c.domain,
                "path": c.path or "/",
                "secure": bool(c.secure),
                "httpOnly": bool(c.has_nonstandard_attr("HttpOnly")),
            }
        )
    return out

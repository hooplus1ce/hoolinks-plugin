"""Antigravity 视觉识别客户端（多模态视觉能力）。

移植自 qa-automation-plugin（vision_antigravity.py），凭据文件复用同一路径
（~/.qa-automation-plugin/antigravity-credentials.json），与参考插件共享 OAuth 授权；
client 凭证从参考插件用户级配置 ~/.qa-automation-plugin/.env 读取。

- 授权: Google OAuth 授权码流（本地回调 51121），凭据含 access/refresh token，过期自动刷新
- 协议: POST {endpoint}/v1internal:streamGenerateContent?alt=sse
  （Gemini 原生 generateContent 格式 + antigravity 客户端信封）
- 模型: gemini-3.6-flash（thinking level 路由 -low/-medium/-high）
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import time
import uuid
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

logger = logging.getLogger("qa_mcp.vision.antigravity")

# 同图同问缓存：键为 (图片字节, 问题, 模型, 思考强度) 的 sha256，值仅存成功结果。
# 验证码（classification）显式 use_cache=False 绕过（一次性答案，不应缓存）。
_IMAGE_CACHE: dict[str, str] = {}


def _cache_key(image_bytes: bytes, question: str, model: str, thinking_level: str) -> str:
    h = hashlib.sha256()
    for part in (
        image_bytes,
        question.encode("utf-8"),
        model.encode("utf-8"),
        (thinking_level or "").encode("utf-8"),
    ):
        h.update(part)
        h.update(b"\x00")
    return h.hexdigest()

# ==================== OAuth 常量（与 oh-my-pi google-antigravity.ts 一致） ====================
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CLOUD_CODE_ENDPOINT = "https://cloudcode-pa.googleapis.com"
DAILY_ENDPOINT = "https://daily-cloudcode-pa.googleapis.com"
SANDBOX_ENDPOINT = "https://daily-cloudcode-pa.sandbox.googleapis.com"
CALLBACK_PORT = 51121
CALLBACK_PATH = "/oauth-callback"
SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/cclog",
    "https://www.googleapis.com/auth/experimentsandconfigs",
]
TIER_LEGACY = "legacy-tier"
PROJECT_ONBOARD_MAX_ATTEMPTS = 5
LOGIN_TIMEOUT_S = 180

ANTIGRAVITY_ENDPOINTS = (DAILY_ENDPOINT, SANDBOX_ENDPOINT)
ANTIGRAVITY_UA = "antigravity/hub/2.1.4 windows/amd64"
MAX_OUTPUT_TOKENS = 65536
DEFAULT_MODEL = "gemini-3.6-flash"

# 逻辑模型 ID → effort 路由 wire ID
ANTIGRAVITY_EFFORT_ROUTING: dict[str, dict[str, str]] = {
    "gemini-3.6-flash": {
        "minimal": "gemini-3.6-flash-low",
        "low": "gemini-3.6-flash-low",
        "medium": "gemini-3.6-flash-medium",
        "high": "gemini-3.6-flash-high",
    },
}
_EFFORT_SUFFIXES = ("-minimal", "-extra-low", "-low", "-medium", "-high")
THINKING_LEVEL_MAP = {
    "minimal": "MINIMAL",
    "low": "LOW",
    "medium": "MEDIUM",
    "high": "HIGH",
    "max": "HIGH",
}

# 参考插件用户级配置（client 凭证所在）
_REF_PLUGIN_ENV = Path.home() / ".qa-automation-plugin" / ".env"


@dataclass
class AntigravityCredentials:
    access_token: str
    refresh_token: str
    expires_at: float
    project_id: str


def _load_ref_env(key: str) -> str:
    """读取参考插件用户级 .env 中的配置（进程环境变量优先）。"""
    val = os.getenv(key, "").strip()
    if val:
        return val
    try:
        for line in _REF_PLUGIN_ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def _oauth_client_credentials() -> tuple[str, str]:
    cid = _load_ref_env("ANTIGRAVITY_CLIENT_ID")
    secret = _load_ref_env("ANTIGRAVITY_CLIENT_SECRET")
    if not cid or not secret:
        raise RuntimeError(
            "未配置 ANTIGRAVITY_CLIENT_ID / ANTIGRAVITY_CLIENT_SECRET，"
            "请写入 ~/.qa-automation-plugin/.env 后重试"
        )
    return cid, secret


def credentials_path() -> Path:
    override = os.getenv("ANTIGRAVITY_CREDENTIALS_FILE", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".qa-automation-plugin" / "antigravity-credentials.json"


def load_credentials() -> Optional[AntigravityCredentials]:
    path = credentials_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return AntigravityCredentials(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=float(data["expires_at"]),
            project_id=data["project_id"],
        )
    except (OSError, json.JSONDecodeError, KeyError):
        return None


def save_credentials(creds: AntigravityCredentials) -> None:
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "access_token": creds.access_token,
                "refresh_token": creds.refresh_token,
                "expires_at": creds.expires_at,
                "project_id": creds.project_id,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    os.chmod(path, 0o600)


def _is_expired(creds: AntigravityCredentials, skew_s: float = 300.0) -> bool:
    return time.time() + skew_s >= creds.expires_at


def refresh_access_token(creds: AntigravityCredentials) -> AntigravityCredentials:
    cid, secret = _oauth_client_credentials()
    resp = httpx.post(
        TOKEN_URL,
        data={
            "client_id": cid,
            "client_secret": secret,
            "refresh_token": creds.refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=30.0,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Antigravity token 刷新失败: HTTP {resp.status_code}: {resp.text[:200]}。"
            f"凭据可能已失效（refresh_token 被吊销/撤销），请重新授权: python -m browser.vision login"
        )
    data = resp.json()
    new = AntigravityCredentials(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token") or creds.refresh_token,
        expires_at=time.time() + float(data.get("expires_in", 3600)) - 300.0,
        project_id=creds.project_id,
    )
    save_credentials(new)
    return new


def ensure_valid_credentials() -> AntigravityCredentials:
    creds = load_credentials()
    if creds is None:
        raise RuntimeError("未找到 Antigravity 凭据。请先运行: python -m browser.vision login")
    if _is_expired(creds):
        return refresh_access_token(creds)
    return creds


# ==================== CCA 流式调用（streamGenerateContent） ====================
def _resolve_endpoints(mode: str) -> list[str]:
    mode = (mode or "auto").lower()
    if mode == "sandbox":
        return [SANDBOX_ENDPOINT]
    if mode == "production":
        return [DAILY_ENDPOINT]
    custom = os.getenv("ANTIGRAVITY_ENDPOINT", "").strip()
    if custom:
        if not custom.startswith(("http://", "https://")):
            logger.warning(f"忽略无效 ANTIGRAVITY_ENDPOINT={custom!r}, 回退默认端点")
            custom = ""
        else:
            return [custom]
    return list(ANTIGRAVITY_ENDPOINTS)


def _route_wire_model(model: str, level: str) -> str:
    table = ANTIGRAVITY_EFFORT_ROUTING.get(model)
    if table:
        return table.get(level, table.get("high", model))
    if any(model.endswith(s) for s in _EFFORT_SUFFIXES):
        return model
    return model


def _cca_headers(access_token: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "User-Agent": ANTIGRAVITY_UA,
    }


def _parse_sse_line(line: str) -> Optional[dict]:
    line = line.strip()
    if not line or not line.startswith("data:"):
        return None
    payload = line[len("data:"):].strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


def stream_vision(
    image_urls: list[dict],
    question: str,
    *,
    model: str = DEFAULT_MODEL,
    thinking_level: str = "low",
    endpoint_mode: str = "auto",
) -> str:
    """调用 Antigravity（gemini-3.6-flash）对图片流式识别，返回文本内容。

    image_urls: [{"url": "data:image/png;base64,..."} | {"url": "https://..."}]。
    无凭据/鉴权失败抛 RuntimeError（提示先登录）。
    """
    creds = ensure_valid_credentials()
    contents: list[dict] = []
    for url_obj in image_urls:
        url = url_obj["url"]
        if url.startswith("data:"):
            header, _, b64 = url.partition(",")
            mime = header[len("data:"):].split(";", 1)[0]
            contents.append({"role": "user", "parts": [{"inlineData": {"mimeType": mime, "data": b64}}]})
        else:
            contents.append({"role": "user", "parts": [{"text": url}]})
    contents.append({"role": "user", "parts": [{"text": question}]})

    generation_config: dict[str, Any] = {"maxOutputTokens": MAX_OUTPUT_TOKENS}
    if thinking_level:
        generation_config["thinkingConfig"] = {
            "thinkingLevel": THINKING_LEVEL_MAP.get(thinking_level, "LOW")
        }

    trajectory_id = str(uuid.uuid4())
    request_id = f"agent/{uuid.uuid4()}/{int(time.time() * 1000)}/{trajectory_id}/1"
    body = {
        "project": creds.project_id,
        "requestId": request_id,
        "request": {
            "contents": contents,
            "sessionId": str(uuid.uuid4()),
            "generationConfig": generation_config,
            "labels": {
                "last_step_index": "0",
                "trajectory_id": trajectory_id,
                "used_claude": "false",
                "used_claude_conservative": "false",
            },
        },
        "model": _route_wire_model(model, thinking_level),
        "userAgent": "antigravity",
        "requestType": "agent",
    }

    content_parts: list[str] = []
    last_error: Optional[Exception] = None
    for endpoint in _resolve_endpoints(endpoint_mode):
        try:
            with httpx.stream(
                "POST",
                f"{endpoint}/v1internal:streamGenerateContent?alt=sse",
                headers=_cca_headers(creds.access_token),
                json=body,
                timeout=httpx.Timeout(connect=10.0, read=120.0, write=60.0, pool=10.0),
            ) as resp:
                if resp.status_code != 200:
                    text = resp.read().decode("utf-8", errors="replace")[:200]
                    raise RuntimeError(f"CCA HTTP {resp.status_code}: {text}")
                for line in resp.iter_lines():
                    event = _parse_sse_line(line)
                    if event is None:
                        continue
                    response = event.get("response") or event
                    candidates = response.get("candidates") or []
                    for cand in candidates:
                        parts = ((cand.get("content") or {}).get("parts")) or []
                        for part in parts:
                            if part.get("text"):
                                content_parts.append(part.get("text", ""))
            return "".join(content_parts).strip()
        except Exception as exc:  # noqa: BLE001 - 端点 failover
            last_error = exc
            logger.warning(f"CCA 端点 {endpoint} 失败: {exc}, 尝试下一个端点")
    assert last_error is not None
    raise last_error


def _mime_from_bytes(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"GIF8":
        return "image/gif"
    return "image/png"


def recognize_image(
    image_bytes: bytes,
    question: str = "请描述图片中的内容",
    *,
    model: str = DEFAULT_MODEL,
    thinking_level: str = "low",
    use_cache: bool = True,
) -> str:
    """识别单张图片字节，返回模型文本。

    use_cache=True 时命中 sha256 缓存直接返回，不重复消耗配额。
    """
    key = _cache_key(image_bytes, question, model, thinking_level) if use_cache else None
    if key is not None and key in _IMAGE_CACHE:
        return _IMAGE_CACHE[key]
    b64 = base64.b64encode(image_bytes).decode()
    data_uri = f"data:{_mime_from_bytes(image_bytes)};base64,{b64}"
    text = stream_vision(
        [{"url": data_uri}], question, model=model, thinking_level=thinking_level
    )
    if key is not None:
        _IMAGE_CACHE[key] = text
    return text


def recognize_image_cached(
    image_bytes: bytes,
    question: str = "请描述图片中的内容",
    *,
    model: str = DEFAULT_MODEL,
    thinking_level: str = "low",
) -> tuple[str, bool]:
    """识别单张图片，返回 (文本, 是否命中缓存)。供工具层透出 cached 标志。"""
    key = _cache_key(image_bytes, question, model, thinking_level)
    if key in _IMAGE_CACHE:
        return _IMAGE_CACHE[key], True
    text = recognize_image(
        image_bytes, question, model=model, thinking_level=thinking_level, use_cache=True
    )
    return text, False


class VisionCaptchaRecognizer:
    """视觉验证码识别器：图片字节 → 视觉模型 → 纯数字验证码。

    替换原 ddddocr OCR 方案；模型 gemini-3.6-flash，识别失败返回空串由调用方重试。
    """

    CAPTCHA_QUESTION = "识别图片中的图形验证码，只输出数字字符，不要输出任何其他文字或解释。"

    def __init__(self, model: str = DEFAULT_MODEL, thinking_level: str = "low") -> None:
        self._model = model
        self._thinking_level = thinking_level

    def classification(self, image_bytes: bytes) -> str:
        """识别验证码图片，返回纯数字串（可能为空）。"""
        try:
            text = recognize_image(
                image_bytes,
                self.CAPTCHA_QUESTION,
                model=self._model,
                thinking_level=self._thinking_level,
                use_cache=False,
            )
        except Exception as exc:  # noqa: BLE001 - 调用方处理重试
            logger.warning("视觉识别验证码失败: %s", exc)
            return ""
        return re.sub(r"\D", "", text)[:6]


# ==================== OAuth 授权码流（CLI 登录入口） ====================
class _CallbackHandler(BaseHTTPRequestHandler):
    code: Optional[str] = None

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != CALLBACK_PATH:
            self.send_error(404)
            return
        params = parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        error = params.get("error", [None])[0]
        if error:
            self._respond(f"授权失败: {error}", 400)
            _CallbackHandler.code = None
        elif code:
            _CallbackHandler.code = code
            self._respond("授权成功! 可以关闭此页面返回终端。", 200)
        else:
            self._respond("缺少 code 参数", 400)

    def _respond(self, text: str, status: int) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # noqa: D102
        pass


def _build_auth_url() -> str:
    cid, _ = _oauth_client_credentials()
    return (
        f"{AUTH_URL}?{urlencode({'client_id': cid, 'redirect_uri': f'http://127.0.0.1:{CALLBACK_PORT}{CALLBACK_PATH}', 'response_type': 'code', 'scope': ' '.join(SCOPES), 'access_type': 'offline', 'prompt': 'consent'})}"
    )


def _run_callback_server() -> Optional[str]:
    server = HTTPServer(("127.0.0.1", CALLBACK_PORT), _CallbackHandler)
    server.timeout = 1.0
    deadline = time.time() + LOGIN_TIMEOUT_S
    try:
        while time.time() < deadline:
            server.handle_request()
            if _CallbackHandler.code:
                return _CallbackHandler.code
    finally:
        server.server_close()
        _CallbackHandler.code = None
    return None


def _exchange_token(code: str) -> tuple[str, str, float]:
    cid, secret = _oauth_client_credentials()
    resp = httpx.post(
        TOKEN_URL,
        data={
            "client_id": cid,
            "client_secret": secret,
            "code": code,
            "redirect_uri": f"http://127.0.0.1:{CALLBACK_PORT}{CALLBACK_PATH}",
            "grant_type": "authorization_code",
        },
        timeout=30.0,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"token 交换失败: HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    return (
        data["access_token"],
        data.get("refresh_token", ""),
        time.time() + float(data.get("expires_in", 3600)) - 300.0,
    )


def _read_project_id(value) -> Optional[str]:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, dict) and isinstance(value.get("id"), str) and value["id"]:
        return value["id"]
    return None


def _discover_project(access_token: str) -> str:
    headers = _cca_headers(access_token)
    load_resp = httpx.post(
        f"{CLOUD_CODE_ENDPOINT}/v1internal:loadCodeAssist",
        headers=headers,
        json={
            "metadata": {
                "ideType": "ANTIGRAVITY",
                "platform": "PLATFORM_UNSPECIFIED",
                "pluginType": "GEMINI",
            }
        },
        timeout=30.0,
    )
    if load_resp.status_code != 200:
        raise RuntimeError(f"loadCodeAssist 失败: HTTP {load_resp.status_code}: {load_resp.text[:200]}")
    payload = load_resp.json()
    existing = _read_project_id(payload.get("cloudaicompanionProject"))
    if existing:
        return existing

    tiers = payload.get("allowedTiers") or []
    tier_id = TIER_LEGACY
    for tier in tiers:
        if isinstance(tier, dict) and tier.get("isDefault") and isinstance(tier.get("id"), str) and tier["id"]:
            tier_id = tier["id"]
            break

    onboard_body = {
        "tierId": tier_id,
        "metadata": {
            "ideType": "ANTIGRAVITY",
            "platform": "PLATFORM_UNSPECIFIED",
            "pluginType": "GEMINI",
        },
    }
    for _ in range(PROJECT_ONBOARD_MAX_ATTEMPTS):
        onboard_resp = httpx.post(
            f"{CLOUD_CODE_ENDPOINT}/v1internal:onboardUser",
            headers=headers,
            json=onboard_body,
            timeout=30.0,
        )
        if onboard_resp.status_code != 200:
            raise RuntimeError(f"onboardUser 失败: HTTP {onboard_resp.status_code}: {onboard_resp.text[:200]}")
        operation = onboard_resp.json()
        if operation.get("done"):
            project_id = _read_project_id(
                (operation.get("response") or {}).get("cloudaicompanionProject")
            )
            if project_id:
                return project_id
    raise RuntimeError(f"onboardUser 在 {PROJECT_ONBOARD_MAX_ATTEMPTS} 次尝试后未返回 projectId")


def login(paste_fallback: bool = True) -> AntigravityCredentials:
    """交互式 OAuth 登录（凭据缺失/失效时执行）：打开授权网页 → 回调收码 → 持久化。

    paste_fallback=False（MCP 工具场景）: 回调超时直接报错，不做 input() 等待
    （工具进程无终端）；CLI 场景保持粘贴授权码兜底。
    """
    url = _build_auth_url()
    print(f"正在打开浏览器进行 Antigravity 授权...\n若浏览器未自动打开, 请手动访问:\n{url}\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    code = _run_callback_server()
    if code is None:
        if not paste_fallback:
            raise RuntimeError("授权超时 (180s)，请重新调用 vision_login 再试")
        code = input("等待回调超时。请粘贴浏览器地址栏中的授权码 (code= 参数值): ").strip()
    if not code:
        raise RuntimeError("未获得授权码, 登录取消")
    access, refresh, expires_at = _exchange_token(code)
    if not refresh:
        raise RuntimeError("授权未返回 refresh_token (需 access_type=offline 同意), 请重试")
    project_id = _discover_project(access)
    creds = AntigravityCredentials(
        access_token=access, refresh_token=refresh, expires_at=expires_at, project_id=project_id
    )
    save_credentials(creds)
    print(f"登录成功! projectId={project_id}, 凭据已保存到 {credentials_path()}")
    return creds


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "login":
        login()
    else:
        print(__doc__)

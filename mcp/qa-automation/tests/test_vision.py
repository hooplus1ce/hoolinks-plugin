"""视觉识别模块测试：模型调用、数字清洗、mime 判断（mock 模型，不依赖网络/凭据）。"""
from __future__ import annotations

import base64

import pytest
from fastmcp import Client

import qa_automation.browser.vision as v
from qa_automation.server import mcp

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
)  # PNG magic + 填充（mime 判断用）
JPEG_BYTES = b"\xff\xd8\xff" + b"\x00" * 8


def test_mime_detection() -> None:
    assert v._mime_from_bytes(PNG_BYTES) == "image/png"
    assert v._mime_from_bytes(JPEG_BYTES) == "image/jpeg"


def test_recognize_image_passes_data_uri(monkeypatch) -> None:
    captured: dict = {}

    def fake_stream(image_urls, question, **kwargs) -> str:
        captured["urls"] = image_urls
        captured["question"] = question
        return "识别结果"

    monkeypatch.setattr(v, "stream_vision", fake_stream)
    text = v.recognize_image(PNG_BYTES, "描述一下")
    assert text == "识别结果"
    url = captured["urls"][0]["url"]
    assert url.startswith("data:image/png;base64,")
    assert captured["question"] == "描述一下"


def test_captcha_recognizer_strips_non_digits(monkeypatch) -> None:
    monkeypatch.setattr(v, "recognize_image", lambda img, q, **kw: "验证码: 7a3b5c1")
    r = v.VisionCaptchaRecognizer()
    assert r.classification(PNG_BYTES) == "7351"


def test_captcha_recognizer_empty_on_model_error(monkeypatch) -> None:
    def boom(img, q, **kw) -> str:
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(v, "recognize_image", boom)
    r = v.VisionCaptchaRecognizer()
    assert r.classification(PNG_BYTES) == ""


def test_route_wire_model() -> None:
    assert v._route_wire_model("gemini-3.6-flash", "low") == "gemini-3.6-flash-low"
    assert v._route_wire_model("gemini-3.6-flash", "high") == "gemini-3.6-flash-high"
    assert v._route_wire_model("claude-x", "high") == "claude-x"


@pytest.fixture
async def client() -> Client:
    async with Client(transport=mcp) as c:
        yield c


def _fake_creds() -> v.AntigravityCredentials:
    return v.AntigravityCredentials(
        access_token="at", refresh_token="rt", expires_at=10**12, project_id="proj-1"
    )


async def test_vision_login_already_valid(client: Client, monkeypatch) -> None:
    monkeypatch.setattr(v, "ensure_valid_credentials", lambda: _fake_creds())
    r = await client.call_tool("vision_login", {})
    assert r.data["ok"] is True
    assert r.data["already_logged_in"] is True
    assert r.data["project_id"] == "proj-1"


async def test_vision_login_runs_oauth(client: Client, monkeypatch) -> None:
    def ensure() -> v.AntigravityCredentials:
        raise RuntimeError("凭据缺失")

    monkeypatch.setattr(v, "ensure_valid_credentials", ensure)
    monkeypatch.setattr(v, "login", lambda paste_fallback=True: _fake_creds())
    r = await client.call_tool("vision_login", {})
    assert r.data["ok"] is True
    assert r.data["already_logged_in"] is False
    assert r.data["project_id"] == "proj-1"


async def test_vision_login_failure(client: Client, monkeypatch) -> None:
    def ensure() -> v.AntigravityCredentials:
        raise RuntimeError("凭据缺失")

    def login(paste_fallback=True) -> v.AntigravityCredentials:
        raise RuntimeError("授权超时")

    monkeypatch.setattr(v, "ensure_valid_credentials", ensure)
    monkeypatch.setattr(v, "login", login)
    r = await client.call_tool("vision_login", {})
    assert r.data["ok"] is False
    assert "授权超时" in r.data["error"]


class _FakeRecognizer:
    def __init__(self, code: str) -> None:
        self._code = code

    def classification(self, img: bytes) -> str:
        return self._code


async def test_captcha_recognize_tool_url(client: Client, monkeypatch) -> None:
    """captcha_recognize（URL 直连模式）：下载验证码图 → 识别返回数字。"""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    PNG = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )

    class _Img(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(PNG)))
            self.end_headers()
            self.wfile.write(PNG)

        def log_message(self, *args) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), _Img)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        # FastMCP FileSystemProvider 以 provider 根（components/）为包链边界导入工具
        # 文件（模块名形如 components.tools.vision，非 qa_automation.components.*），
        # 因此工具模块级全局（如 _recognizer 工厂）在自然包名上 patch 不生效；改打
        # 识别器类（browser.vision 经自然导入，两处共享同一模块对象）。
        monkeypatch.setattr(v, "VisionCaptchaRecognizer", lambda **kw: _FakeRecognizer("4321"))
        r = await client.call_tool(
            "captcha_recognize", {"url": f"http://127.0.0.1:{server.server_port}/c.png"}
        )
        assert r.data["ok"] is True
        assert r.data["captcha"] == "4321"
    finally:
        server.shutdown()
        server.server_close()

"""验证码登录核心逻辑测试：本地 mock SCM 服务 + 假 OCR，全链路真实运行。"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

import pytest
from fastmcp import Client

from qa_automation.browser.lifecycle import PlaywrightLifecycle
from qa_automation.browser.login import LoginError, captcha_login
from qa_automation.server import mcp

_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00"
    b"\x00\x00\x03\x00\x01\xff\xff\xff\xff\x00\x00\x00\x00IEND\xaeB`\x82"
)


class _MockSCM(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.startswith("/validateCode.json"):
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(_PNG)))
            self.end_headers()
            self.wfile.write(_PNG)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        data = parse_qs(self.rfile.read(length).decode())
        payload = (
            json.dumps({"code": 0}).encode()
            if data.get("vcode", [""])[0] == "1234"
            else json.dumps({"code": 1, "msg": "bad vcode"}).encode()
        )
        self.send_response(200)
        if data.get("vcode", [""])[0] == "1234":
            self.send_header("Set-Cookie", "SCM_SESSION=abc123; Path=/; HttpOnly")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args) -> None:  # 静默访问日志
        pass


class _MockSCMJson(BaseHTTPRequestHandler):
    """SCM 风格：JSON body + ok 响应（userName/userPwd/vcode 字段）。"""

    def do_GET(self) -> None:
        if self.path.startswith("/scmpsm/login/validateCode") and "random=" in self.path:
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(_PNG)))
            self.end_headers()
            self.wfile.write(_PNG)
        elif self.path == "/static/admin":
            body = b"<html><body><h1>Workbench</h1></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        data = json.loads(self.rfile.read(length).decode())
        ok = data.get("vcode") == "1234"
        payload = json.dumps(
            {
                "msg": "ok" if ok else "验证码错误",
                "ok": ok,
                "status": 0 if ok else -1,
                "data": "token-abc123" if ok else None,
            }
        ).encode()
        self.send_response(200)
        if ok:
            self.send_header("Set-Cookie", "SCM_SESSION=abc123; Path=/; HttpOnly")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args) -> None:
        pass


class _FakeOcr:
    """模拟 ddddocr 接口（classification(image_bytes) -> str）。"""

    def __init__(self, code: str) -> None:
        self._code = code

    def classification(self, img: bytes) -> str:
        return self._code


@pytest.fixture
def mock_scm() -> str:
    server = HTTPServer(("127.0.0.1", 0), _MockSCM)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def mock_scm_json() -> str:
    server = HTTPServer(("127.0.0.1", 0), _MockSCMJson)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


async def test_captcha_login_core(mock_scm: str) -> None:
    cookies = await captcha_login(mock_scm, "admin", "pwd", _FakeOcr("1234"))
    by_name = {c["name"]: c for c in cookies}
    assert "SCM_SESSION" in by_name
    assert by_name["SCM_SESSION"]["value"] == "abc123"
    assert by_name["SCM_SESSION"]["domain"] == "127.0.0.1"
    assert by_name["SCM_SESSION"]["path"] == "/"
    assert by_name["SCM_SESSION"]["httpOnly"] is True
    # SESSION 预置 cookie 一并注入（服务端会话绑定）
    assert any(c["name"] == "SESSION" for c in cookies)


async def test_captcha_login_rejected_vcode(mock_scm: str) -> None:
    with pytest.raises(LoginError, match="rejected"):
        await captcha_login(mock_scm, "admin", "pwd", _FakeOcr("9999"))


async def test_captcha_login_scm_mode(mock_scm_json: str) -> None:
    """SCM（demo18）模式：JSON body + userName/userPwd/vcode 字段 + ok 校验 + random 参数。"""
    from qa_automation.browser.login import SCM_LOGIN_CONFIG

    cookies = await captcha_login(
        mock_scm_json,
        "admin",
        "pwd",
        _FakeOcr("1234"),
        **SCM_LOGIN_CONFIG,
        validate_code_extra_params={"random": "0.123456"},
    )
    by_name = {c["name"]: c for c in cookies}
    assert by_name["SCM_SESSION"]["value"] == "abc123"
    assert by_name["SCM_SESSION"]["httpOnly"] is True
    # 响应体 data token -> HL-Access-Token cookie（前端拦截器从同名 cookie 读值放同名 header）
    assert by_name["HL-Access-Token"]["value"] == "token-abc123"

    with pytest.raises(LoginError, match="rejected"):
        await captcha_login(
            mock_scm_json,
            "admin",
            "pwd",
            _FakeOcr("9999"),
            **SCM_LOGIN_CONFIG,
            validate_code_extra_params={"random": "0.123456"},
        )


async def test_captcha_login_real_scm_rejected() -> None:
    """真实 demo18-scm 接口全链路（fake 账号）：验证码获取 + 字段/JSON 格式 + ok 校验。

    预期被服务端拒绝（用户不存在）；SCM 不可达时跳过。
    """
    from qa_automation.browser.login import SCM_LOGIN_CONFIG

    try:
        await captcha_login(
            "https://demo18-scm.hoolinks.com",
            "__probe__",
            "__probe__",
            _FakeOcr("0000"),
            **SCM_LOGIN_CONFIG,
            validate_code_extra_params={"random": "0.1"},
        )
        pytest.skip("probe unexpectedly succeeded (unexpected SCM state)")
    except LoginError as exc:
        assert "rejected" in str(exc)
    except Exception as exc:  # 网络不可达等
        pytest.skip(f"SCM unreachable: {exc}")


async def test_captcha_login_inject_into_session(mock_scm: str) -> None:
    """核心登录 + 生命周期会话注入：cookie 在浏览器会话内可见。"""
    lc = PlaywrightLifecycle()
    await lc.launch(headless=True)
    try:
        await lc.create_session("s", use_default=False)
        cookies = await captcha_login(mock_scm, "admin", "pwd", _FakeOcr("1234"))
        await lc.context("s").add_cookies(cookies)
        got = {c["name"]: c["value"] for c in await lc.context("s").cookies()}
        assert got.get("SCM_SESSION") == "abc123"
    finally:
        await lc.close()


@pytest.fixture
async def client() -> Client:
    async with Client(transport=mcp) as c:
        yield c


async def test_login_tool_missing_config(client: Client, monkeypatch) -> None:
    monkeypatch.delenv("SCM_BASE_URL", raising=False)
    monkeypatch.delenv("SCM_USERNAME", raising=False)
    monkeypatch.delenv("SCM_USERPWD", raising=False)
    monkeypatch.delenv("SCM_USERNAME2", raising=False)
    monkeypatch.delenv("SCM_USERPWD2", raising=False)
    r = await client.call_tool("login_with_captcha", {"session": "x"})
    assert r.data["ok"] is False
    assert "missing" in r.data["error"]


async def test_login_tool_account2_missing(client: Client, monkeypatch, tmp_path) -> None:
    """account 名在 accounts.json 不存在 → 明确报错。"""
    monkeypatch.setenv("SCM_BASE_URL", "https://demo18-scm.hoolinks.com")
    monkeypatch.setattr(
        "qa_automation.browser.accounts.ACCOUNTS_FILE", tmp_path / "accounts.json"
    )
    r = await client.call_tool("login_with_captcha", {"session": "x", "account": "nope"})
    assert r.data["ok"] is False
    assert "not found" in r.data["error"]


async def test_login_tool_session_error(client: Client) -> None:
    r = await client.call_tool(
        "login_with_captcha",
        {"session": "x", "username": "u", "password": "p", "base_url": "http://127.0.0.1:1"},
    )
    assert r.data["ok"] is False
    assert "connect" in r.data["error"].lower()


async def test_api_login_and_inject_flow(mock_scm_json: str) -> None:
    """API 直登全流程：下载验证码 → 识别 → 登录 → cookies 注入会话 → goto 工作台。"""
    from qa_automation.browser.lifecycle import PlaywrightLifecycle
    from qa_automation.browser.login import api_login_and_inject

    lc = PlaywrightLifecycle()
    await lc.launch(headless=True)
    try:
        await lc.create_session("s", use_default=False)
        cookies = await api_login_and_inject(
            lc, "s", mock_scm_json, "admin", "pwd", _FakeOcr("1234")
        )
        names = {c["name"] for c in cookies}
        assert "SCM_SESSION" in names and "HL-Access-Token" in names
        # cookies 已注入会话
        got = {c["name"]: c["value"] for c in await lc.context("s").cookies()}
        assert got.get("SCM_SESSION") == "abc123"
        # 已 goto 工作台
        page = await lc.page("s")
        assert "/static/admin" in page.url
    finally:
        await lc.close()


async def test_api_login_retries_on_reject(mock_scm_json: str) -> None:
    """验证码被拒（识别错）自动重试，最终成功。"""
    from qa_automation.browser.lifecycle import PlaywrightLifecycle
    from qa_automation.browser.login import api_login_and_inject

    class _FlakyRecognizer(_FakeOcr):
        def __init__(self) -> None:
            super().__init__("1234")
            self.calls = 0

        def classification(self, img: bytes) -> str:
            self.calls += 1
            return "9999" if self.calls == 1 else "1234"  # 第一次识别错误

    lc = PlaywrightLifecycle()
    await lc.launch(headless=True)
    try:
        await lc.create_session("s", use_default=False)
        recognizer = _FlakyRecognizer()
        cookies = await api_login_and_inject(
            lc, "s", mock_scm_json, "admin", "pwd", recognizer
        )
        assert recognizer.calls >= 2
        assert {c["name"] for c in cookies} >= {"SCM_SESSION"}
    finally:
        await lc.close()

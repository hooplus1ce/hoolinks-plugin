"""pytest 共享 fixture。

cdp_url: 提供可接管的 CDP 端点——优先探测 9222 日常浏览器真实可 attach
（MV3 扩展 service worker 活跃时 Playwright attach 会失败，见
browser.lifecycle._friendly_attach_error）；不可用时自启一个带
--remote-debugging-port 的临时 Chrome（独立 profile，无头）兜底，测试自足。
"""
from __future__ import annotations

import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import pytest
import pytest_asyncio

from qa_automation.browser.lifecycle import PlaywrightLifecycle

CHROME_CANDIDATES = (
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
)


def _port_open(host: str, port: int, timeout: float = 0.05) -> bool:
    with socket.socket() as s:
        s.settimeout(timeout)
        try:
            s.connect((host, port))
            return True
        except OSError:
            return False


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def session_cdp_server() -> str | None:
    """Session 级别的 CDP 端点提供器：启动独立无扩展无头 Chrome 实例供整个测试套件复用。"""
    exe = next((p for p in CHROME_CANDIDATES if p.exists()), None)
    if exe is None:
        yield None
        return

    port = _free_port()
    profile = tempfile.mkdtemp(prefix="pw-attach-session-")
    proc = subprocess.Popen(
        [
            str(exe),
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--headless=new",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-sync",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(60):
        if _port_open("127.0.0.1", port, timeout=0.05):
            break
        time.sleep(0.05)

    if not _port_open("127.0.0.1", port, timeout=0.05):
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        shutil.rmtree(profile, ignore_errors=True)
        yield None
        return

    url = f"http://127.0.0.1:{port}"
    try:
        yield url
    finally:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        shutil.rmtree(profile, ignore_errors=True)


@pytest.fixture
def cdp_url(session_cdp_server: str | None) -> str:
    """CDP 端点 fixture（快速复用 session 级干净无头 Chrome 实例）。"""
    if session_cdp_server is None:
        pytest.skip("Chrome executable not found for fallback")
    return session_cdp_server

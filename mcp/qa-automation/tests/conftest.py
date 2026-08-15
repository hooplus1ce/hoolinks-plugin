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


def _port_open(host: str, port: int, timeout: float = 0.5) -> bool:
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


async def _spawn_debug_chrome() -> str | None:
    exe = next((p for p in CHROME_CANDIDATES if p.exists()), None)
    if exe is None:
        return None
    port = _free_port()
    profile = tempfile.mkdtemp(prefix="pw-attach-test-")
    # 有头窗口会在用户屏幕上弹出并被测试强杀，造成"浏览器被关闭"观感——固定无头。
    proc = subprocess.Popen(
        [
            str(exe),
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--headless=new",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(50):
        if _port_open("127.0.0.1", port):
            break
        time.sleep(0.2)
    if not _port_open("127.0.0.1", port):
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        shutil.rmtree(profile, ignore_errors=True)
        return None
    _spawn_debug_chrome._cleanup = (proc, profile)  # type: ignore[attr-defined]
    return f"http://127.0.0.1:{port}"


@pytest_asyncio.fixture
async def cdp_url() -> str:
    """CDP 端点：9222 真实可 attach 则用之；否则自启临时无头 Chrome 兜底。"""
    probe = PlaywrightLifecycle()
    try:
        await probe.attach(timeout_ms=5_000)
    except Exception:
        await probe.close()
        url = await _spawn_debug_chrome()
        if url is None:
            pytest.skip("9222 not attachable and no Chrome executable for fallback")
        yield url
        cleanup = getattr(_spawn_debug_chrome, "_cleanup", None)
        if cleanup:
            proc, profile = cleanup
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
            shutil.rmtree(profile, ignore_errors=True)
        return
    else:
        await probe.close()
        yield "http://127.0.0.1:9222"

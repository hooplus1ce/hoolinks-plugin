"""文件传输 MCP 工具集：下载（CDP 重定向 + xlsx 预览）与上传（set_input_files / filechooser）。

对齐 qa-automation-plugin 的 download_file_impl / upload_file_impl，适配本扁平项目：
- browser_mgr -> PlaywrightLifecycle（`page = await lc.page(session)`）；
- frame 解析 -> `_resolve_locator`（顶层优先、激活 iframe 兜底，`return_frame=True` 拿命中 Frame）；
- 下载用 CDP `Browser.setDownloadBehavior` 重定向到 DOWNLOAD_DIR，完成后恢复浏览器默认行为；
- 下载的 xlsx 文件支持 openpyxl 读取预览（read_preview），APS/WMS 导出断言高频场景。
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from fastmcp import Context
from fastmcp.tools import tool
from mcp.types import Icon

from qa_automation.browser.excel_io import read_xlsx
from qa_automation.components.tools.browser import _err, _lifecycle, _resolve_locator
from qa_automation.components.tools.wait import _check_wait_condition
from qa_automation.config import (
    ACTION_RETRY_ATTEMPTS,
    ACTION_RETRY_BACKOFF_MS,
    DOWNLOAD_DIR,
    ELEMENT_WAIT_TIMEOUT_MS,
    PROJECT_DIR,
)

_TRANSFER_ICON = Icon(
    src="data:image/svg+xml;base64,PHN2ZyB4bWxucz0naHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmcnIHdpZHRoPScyNCcgaGVpZ2h0PScyNCc+PHBhdGggZD0nTTEyIDNWMTVNNyAxMGw1IDUgNS01JyBmaWxsPSdub25lJyBzdHJva2U9J3doaXRlJyBzdHJva2Utd2lkdGg9JzEuNScgc3Ryb2tlLWxpbmVjYXA9J3JvdW5kJyBzdHJva2UtbGluZWpvaW49J3JvdW5kJy8+PHBhdGggZD0nTTQgMjFIMjAnIGZpbGw9J25vbmUnIHN0cm9rZT0nd2hpdGUnIHN0cm9rZS13aWR0aD0nMS41JyBzdHJva2UtbGluZWNhcD0ncm91bmQnLz48L3N2Zz4=",
    mime_type="image/svg+xml",
)

DEFAULT_DOWNLOAD_WAIT_MS = 30000
_XLSX_EXT = (".xlsx", ".xlsm")


def _resolve_download_dir(download_dir: str | None) -> str:
    """解析下载保存目录：相对路径锚定 PROJECT_DIR（而非进程 cwd），绝对路径原样。"""
    base = download_dir or str(DOWNLOAD_DIR)
    path = (
        os.path.abspath(base)
        if os.path.isabs(base)
        else os.path.abspath(os.path.join(PROJECT_DIR, base))
    )
    os.makedirs(path, exist_ok=True)
    return path


def _resolve_upload_paths(file_paths: list[str]) -> list[str]:
    """上传文件路径解析：相对路径优先基于 PROJECT_DIR，其次进程 cwd；必须存在。"""
    if not file_paths:
        raise RuntimeError("file_paths 不能为空")
    resolved: list[str] = []
    missing: list[str] = []
    for p in file_paths:
        if os.path.isabs(p):
            candidates = [os.path.abspath(p)]
        else:
            candidates = [
                os.path.abspath(os.path.join(PROJECT_DIR, p)),
                os.path.abspath(p),
            ]
        found = next((c for c in candidates if os.path.isfile(c)), None)
        if found is None:
            missing.append(p)
        else:
            resolved.append(found)
    if missing:
        raise RuntimeError(f"待上传文件不存在: {missing}")
    return resolved


async def _retry_locator_action(label: str, fn) -> Any:
    """定位-执行统一重试（SPA 重渲染/元素 detach/短暂遮挡），返回最后一次结果或抛错。"""
    last: Exception | None = None
    for attempt in range(1, ACTION_RETRY_ATTEMPTS + 1):
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001 - 重试后统一抛最后一次
            last = exc
            if attempt < ACTION_RETRY_ATTEMPTS:
                await asyncio.sleep(ACTION_RETRY_BACKOFF_MS / 1000)
    assert last is not None
    raise last


@tool(
    title="File: Download",
    description="点击触发下载的按钮/链接，经 CDP 将下载文件重定向保存到指定目录并验证落盘；"
    "xlsx 文件自动读取预览（openpyxl），供导出报表断言。定位参数同 page_interact："
    "role/name/text/css/xpath 任选其一。download_dir 默认 downloads（相对项目根，可 DOWNLOAD_DIR 覆盖）；"
    "filename 可指定保存名（同名覆盖）；read_preview=True 时对 xlsx/xlsm 返回内容预览。",
    icons=[_TRANSFER_ICON],
    tags={"browser", "file", "download"},
)
async def download_file(
    ctx: Context,
    session: str | None = None,
    role: str | None = None,
    name: str | None = None,
    text: str | None = None,
    css: str | None = None,
    xpath: str | None = None,
    download_dir: str | None = None,
    filename: str | None = None,
    wait_timeout_ms: int = DEFAULT_DOWNLOAD_WAIT_MS,
    read_preview: bool = True,
) -> dict:
    """点击下载按钮/链接，重定向下载到目录并验证落盘。

    Args:
        session: 目标会话名（多账号场景必须显式指定；缺省使用激活会话）。
        role/name/text/css/xpath: 定位维度（任选其一，语义优先）。
        download_dir: 保存目录（默认 downloads）。
        filename: 指定保存名（Chrome 重名会加 "(1)"，工具统一改回；同名覆盖）。
        wait_timeout_ms: 下载完成等待上限（默认 30s）。
        read_preview: xlsx/xlsm 下载后是否读取内容预览（默认 true）。
    """
    lc = _lifecycle(ctx)
    try:
        page = await lc.page(session)
    except Exception as exc:
        return _err(exc)

    timeout = max(1000, min(int(wait_timeout_ms), 120000))
    save_dir = _resolve_download_dir(download_dir)
    started = time.monotonic()

    async def _resolve_click_target():
        locator, _ = await _resolve_locator(
            page,
            role=role,
            name=name,
            text=text,
            css=css,
            xpath=xpath,
            in_iframe=True,
            return_frame=True,
        )
        await locator.wait_for(state="visible", timeout=ELEMENT_WAIT_TIMEOUT_MS)
        return locator

    locator = await _resolve_click_target()

    cdp = await page.context.browser.new_browser_cdp_session()
    begin_info: dict[str, dict[str, Any]] = {}
    state_map: dict[str, str] = {}
    done = asyncio.Event()

    def _on_will_begin(payload: dict[str, Any]) -> None:
        guid = str(payload.get("guid", ""))
        begin_info[guid] = {
            "guid": guid,
            "suggested_filename": payload.get("suggestedFilename"),
            "url": payload.get("url"),
        }

    def _on_progress(payload: dict[str, Any]) -> None:
        guid = str(payload.get("guid", ""))
        if guid:
            state_map[guid] = payload.get("state")
        done.set()

    cdp.on("Browser.downloadWillBegin", _on_will_begin)
    cdp.on("Browser.downloadProgress", _on_progress)
    try:
        await cdp.send(
            "Browser.setDownloadBehavior",
            {"behavior": "allow", "downloadPath": save_dir, "eventsEnabled": True},
        )
        pre_files = set(os.listdir(save_dir))
        try:
            await locator.click(timeout=max(ELEMENT_WAIT_TIMEOUT_MS, 5000))
        except Exception:  # 点击动作 actionability 超时（遮挡等）→ 中心坐标兜底
            box = await locator.bounding_box()
            if box is None:
                raise
            await page.mouse.click(
                box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            )

        async def _await_downloads() -> None:
            # 事件驱动：下载进度事件到达即重查终态；0.2s 轮询兜底（防事件丢失）。
            while not (
                begin_info
                and all(state_map.get(g) in ("completed", "canceled") for g in begin_info)
            ):
                done.clear()
                try:
                    await asyncio.wait_for(done.wait(), timeout=0.2)
                except asyncio.TimeoutError:
                    continue

        try:
            await asyncio.wait_for(_await_downloads(), timeout=timeout / 1000)
        except asyncio.TimeoutError:
            pass  # 超时交给下方 finished 判断统一收尾
    finally:
        try:
            await cdp.send(
                "Browser.setDownloadBehavior",
                {"behavior": "default", "eventsEnabled": False},
            )
        except Exception:  # noqa: BLE001 - 恢复失败仅告警
            pass
        try:
            await cdp.detach()
        except Exception:  # noqa: BLE001
            pass

    elapsed_ms = int((time.monotonic() - started) * 1000)

    if not begin_info:
        return {
            "ok": True,
            "status": "no_download",
            "note": "点击未触发下载（页面无 download 事件）；请核对按钮定位或确认下载由其他交互触发",
            "elapsed_ms": elapsed_ms,
            "download_dir": save_dir,
            "files": [],
        }

    new_files = sorted(set(os.listdir(save_dir)) - pre_files)
    if filename and new_files:
        src = os.path.join(save_dir, new_files[0])
        dst = os.path.join(save_dir, filename)
        if src != dst:
            try:
                os.replace(src, dst)
                new_files[0] = filename
            except OSError:
                pass

    files_info = []
    for f in new_files:
        fp = os.path.join(save_dir, f)
        try:
            size = os.path.getsize(fp)
        except OSError:
            size = None
        files_info.append({"filename": f, "path": fp, "size_bytes": size})

    pending = [g for g in begin_info if state_map.get(g) not in ("completed", "canceled")]
    canceled = [g for g in begin_info if state_map.get(g) == "canceled"]
    if pending:
        status = "timeout"
        note = f"下载未在 {timeout}ms 内完成（pending: {len(pending)} 个）"
    elif canceled and not any(state_map.get(g) == "completed" for g in begin_info):
        status = "canceled"
        note = "下载被浏览器取消"
    else:
        status = "success"
        note = "下载完成，文件已落盘"

    result: dict[str, Any] = {
        "ok": True,
        "status": status,
        "download_dir": save_dir,
        "files": files_info,
        "elapsed_ms": elapsed_ms,
        "note": note,
    }

    # xlsx/xlsm 读取预览（导出报表断言）：仅成功落盘且显式开启时
    if status == "success" and read_preview and files_info:
        first = files_info[0]
        if first["filename"].lower().endswith(_XLSX_EXT):
            try:
                result["preview"] = read_xlsx(first["path"])
            except Exception as exc:  # noqa: BLE001 - 预览失败不影响下载结果
                result["preview_error"] = f"{type(exc).__name__}: {exc}"
    return result


@tool(
    title="File: Upload",
    description="上传文件到指定按钮/输入框。两条路径：定位到 <input type=file> 直接 set_input_files；"
    "定位到普通按钮则拦截系统文件选择框（filechooser）注入文件，不弹原生对话框。"
    "file_paths 为待上传文件（相对项目根，必须存在）；success_text 可选，指定后轮询等待上传成功反馈。"
    "定位参数同 page_interact：role/name/text/css/xpath 任选其一。",
    icons=[_TRANSFER_ICON],
    tags={"browser", "file", "upload"},
)
async def upload_file(
    ctx: Context,
    file_paths: list[str],
    session: str | None = None,
    role: str | None = None,
    name: str | None = None,
    text: str | None = None,
    css: str | None = None,
    xpath: str | None = None,
    success_text: str | None = None,
    wait_timeout_ms: int = 15000,
) -> dict:
    """上传文件到按钮/输入框，可选等待成功反馈。

    Args:
        file_paths: 一个或多个文件（相对项目根或绝对路径，必须存在）。
        session: 目标会话名。
        role/name/text/css/xpath: 定位维度（任选其一）。
        success_text: 上传成功后页面出现的文本（如"上传成功"），指定后轮询等待。
        wait_timeout_ms: success_text 等待上限（默认 15s）。
    """
    lc = _lifecycle(ctx)
    try:
        page = await lc.page(session)
        paths = _resolve_upload_paths(file_paths)
    except Exception as exc:
        return _err(exc)

    frame_note: str | None = None
    started = time.monotonic()

    async def _set_files_once():
        nonlocal frame_note
        locator, frame = await _resolve_locator(
            page,
            role=role,
            name=name,
            text=text,
            css=css,
            xpath=xpath,
            in_iframe=True,
            return_frame=True,
        )
        frame_note = "iframe" if frame is not page else "top"
        await locator.wait_for(state="visible", timeout=ELEMENT_WAIT_TIMEOUT_MS)
        try:
            await asyncio.wait_for(locator.scroll_into_view_if_needed(), timeout=5)
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001 - 滚动失败不阻断
            pass
        is_file_input = await locator.evaluate(
            "el => el.tagName === 'INPUT' && el.type === 'file'"
        )
        if is_file_input:
            await locator.set_input_files(paths)
            return {"mode": "set_input_files"}
        async with page.expect_file_chooser(
            timeout=ELEMENT_WAIT_TIMEOUT_MS
        ) as fc_info:
            await locator.click(timeout=max(ELEMENT_WAIT_TIMEOUT_MS, 5000))
        chooser = await fc_info.value
        await chooser.set_files(paths)
        return {"mode": "filechooser", "is_multiple": chooser.is_multiple()}

    try:
        set_result = await _retry_locator_action("上传", _set_files_once)
    except Exception as exc:
        return _err(exc)

    result: dict[str, Any] = {
        "ok": True,
        "status": "success",
        **set_result,
        "file_paths": paths,
        "frame": frame_note,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }

    if success_text:
        found = False
        detail: dict[str, Any] = {}
        deadline = time.monotonic() + wait_timeout_ms / 1000
        while time.monotonic() < deadline:
            check = await _check_wait_condition(
                page, "text_present", None, success_text, False
            )
            detail = check.get("detail", {})
            if check.get("met"):
                found = True
                break
            await asyncio.sleep(0.3)
        result["success_text"] = success_text
        result["success_text_found"] = found
        result["success_check"] = detail
    return result

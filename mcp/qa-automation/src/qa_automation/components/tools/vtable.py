"""VTable (canvas 渲染表格) MCP 工具集。

基于 FastMCP FileSystemProvider 的独立 @tool 装饰器（组件与服务器零耦合）。
对齐 qa-automation-plugin 的 vtable provider，适配本扁平项目：
- 通过 Context 注入的 PlaywrightLifecycle 取 page（多账号场景用 session 参数路由）；
- 用 components.tools.browser._active_iframe_frame 定位持有 VTable 的 iframe，
  无则回退顶层 page；
- 全部坐标均为顶层视口坐标，供 page.mouse.click(x, y) 直接使用；
- 统一返回 {"ok": bool, ...}，错误不抛协议异常，便于 QA 断言。
"""
from __future__ import annotations

from fastmcp import Context
from fastmcp.tools import tool
from mcp.types import Icon

from qa_automation.browser import vtable as vt
from qa_automation.browser.lifecycle import PlaywrightLifecycle
from qa_automation.components.tools.browser import _active_iframe_frame, _err, _lifecycle

_VTABLE_ICON = Icon(
    src="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0Ij48cmVjdCB4PSIzIiB5PSI0IiB3aWR0aD0iMTgiIGhlaWdodD0iMTYiIHJ4PSIyIiBmaWxsPSJub25lIiBzdHJva2U9IiMyNTYzZWIiIHN0cm9rZS13aWR0aD0iMS41Ii8+PHBhdGggZD0iTTMgMTBoMThNOSA0djE2IiBmaWxsPSJub25lIiBzdHJva2U9IiMyNTYzZWIiIHN0cm9rZS13aWR0aD0iMS41Ii8+PC9zdmc+",
    mime_type="image/svg+xml",
)


async def _vtable_host(ctx: Context, session: str | None) -> tuple:
    """解析 VTable 所在 host (持有 iframe 的 Frame 或顶层 page) 与顶层 page。

    host 用于执行 JS（window._vtable 挂载/读取）；page 用于 page.mouse 真实鼠标事件。
    """
    lc: PlaywrightLifecycle = _lifecycle(ctx)
    page = await lc.page(session)
    frame = await _active_iframe_frame(page)
    host = frame if frame is not None else page
    return host, page


@tool(
    title="VTable: Refresh Instance",
    description="刷新目标页面中最新 VTable 实例（window._vtable），作为其他 vtable 操作的前置条件。",
    icons=[_VTABLE_ICON],
    tags={"vtable", "browser", "qa"},
)
async def vtable_refresh_instance(ctx: Context, session: str | None = None) -> dict:
    """刷新并挂载最新 VTable 实例至 window._vtable。"""
    try:
        host, _ = await _vtable_host(ctx, session)
        result = await vt.refresh_instance(host)
        return {"ok": True, **result}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@tool(
    title="VTable: Analyze Headers",
    description="分析 VTable 列头与单元格内交互图标：header_icons 为表头行真实渲染图标（排序/筛选/下拉/冻结等，含顶层视口坐标可直接点击）；cell_icons 为已渲染 body 单元格内图标（行内按钮/链接/checkbox/开关等，columns 配置中不存在，仅场景图可得）；capabilities 汇总 sortable/filterable/interactiveCell。用于规划列头点击与行内交互。",
    icons=[_VTABLE_ICON],
    tags={"vtable", "browser", "qa"},
)
async def vtable_analyze_headers(ctx: Context, session: str | None = None) -> dict:
    """分析 VTable 列头与单元格内交互图标。"""
    try:
        host, _ = await _vtable_host(ctx, session)
        columns = await vt.analyze_headers(host)
        return {"ok": True, "columns": columns}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@tool(
    title="VTable: Scan Columns",
    description="扫描 VTable 全部列（含多级表头）：返回每列标题、body 行为分类（checkbox/button/文本等）及表头图标的顶层视口坐标 viewportX/viewportY，坐标可直接传给 page.mouse.click(x, y)。用于规划列头点击（排序/筛选/下拉）与列交互。",
    icons=[_VTABLE_ICON],
    tags={"vtable", "browser", "qa"},
)
async def vtable_scan_columns(
    ctx: Context, session: str | None = None, max_col: int = 200
) -> dict:
    """扫描 VTable 全部列（含多级表头）。"""
    try:
        host, _ = await _vtable_host(ctx, session)
        columns = await vt.scan_columns(host, max_col)
        return {"ok": True, "columns": columns}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@tool(
    title="VTable: Get Row Count",
    description="读取当前 VTable 纯数据行数（内部记录集合长度，不受屏幕滚动截断影响）。",
    icons=[_VTABLE_ICON],
    tags={"vtable", "browser", "qa"},
)
async def vtable_get_row_count(ctx: Context, session: str | None = None) -> dict:
    """读取当前 VTable 纯数据行数。"""
    try:
        host, _ = await _vtable_host(ctx, session)
        count = await vt.get_row_count(host)
        return {"ok": True, "count": count}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@tool(
    title="VTable: Get All Records",
    description="一次性读取表格中全部完整后台行记录（JSON），用于整表断言和宏观数据检查。",
    icons=[_VTABLE_ICON],
    tags={"vtable", "browser", "qa"},
)
async def vtable_get_all_records(ctx: Context, session: str | None = None) -> dict:
    """一次性读取表格所有后台完整记录对象。"""
    try:
        host, _ = await _vtable_host(ctx, session)
        records = await vt.get_all_records(host)
        return {"ok": True, "records": records}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@tool(
    title="VTable: Get Cell Text",
    description="读取单元格值。row_index 为纯数据行号（0 为第一行），col_field 支持字段名或列标题。visual=True（默认）读渲染层文本，与界面一致——排序/筛选仅在渲染层，须用它才能读真实顺序；visual=False 读数据源原始值（忽略排序/筛选）。单元格含多个文本节点（如操作列的并排链接）时返回字符串数组。视口外的行需先滚动。",
    icons=[_VTABLE_ICON],
    tags={"vtable", "browser", "qa"},
)
async def vtable_get_cell_text(
    ctx: Context,
    row_index: int,
    col_field: str,
    session: str | None = None,
    visual: bool = True,
) -> dict:
    """读取某个具体单元格的值。"""
    try:
        host, _ = await _vtable_host(ctx, session)
        text = await vt.get_cell_text(host, row_index, col_field, visual)
        return {
            "ok": True,
            "row_index": row_index,
            "col_field": col_field,
            "text": text,
        }
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@tool(
    title="VTable: Get Column Values",
    description="按列标题读取该列所有单元格值。titles 为列标题数组；raw=false 读渲染层视觉文本（与界面一致），raw=true 读原始字段值。返回每列值列表及缺失列。单元格含多个文本节点时该项为字符串数组（如操作列的多个链接）。",
    icons=[_VTABLE_ICON],
    tags={"vtable", "browser", "qa"},
)
async def vtable_get_column_values(
    ctx: Context,
    titles: list[str],
    session: str | None = None,
    raw: bool = False,
) -> dict:
    """按中文列标题读取该列所有单元格值。"""
    try:
        host, _ = await _vtable_host(ctx, session)
        result = await vt.get_column_values(host, titles, raw)
        return {"ok": True, **result}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@tool(
    title="VTable: Get Cell Render Info",
    description="读取单元格渲染详情：视觉文本、文字/背景/边框色、字体大小及文本/背景节点（detail=\"full\" 含全部节点）。col_field 支持列索引或字段名/列标题，row_index 为纯数据行号（0 为第一行）。用于断言展示样式（标签色、高亮等）。",
    icons=[_VTABLE_ICON],
    tags={"vtable", "browser", "qa"},
)
async def vtable_get_cell_render_info(
    ctx: Context,
    col_field: int | str,
    row_index: int,
    session: str | None = None,
    detail: str = "basic",
) -> dict:
    """读取某个单元格的场景图渲染信息。"""
    try:
        host, _ = await _vtable_host(ctx, session)
        result = await vt.get_cell_render_info(host, col_field, row_index, detail)
        return {"ok": True, **result}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@tool(
    title="VTable: Get Cell Center",
    description="读取单元格中心顶层视口坐标 viewportX/viewportY（含 iframe 与容器偏移），可直接作为 page.mouse.click(x, y) 点击坐标。col_field 支持列索引或字段名/列标题，row_index 为纯数据行号（0 为第一行）。",
    icons=[_VTABLE_ICON],
    tags={"vtable", "browser", "qa"},
)
async def vtable_get_cell_center(
    ctx: Context,
    col_field: int | str,
    row_index: int,
    session: str | None = None,
) -> dict:
    """读取单元格中心顶层视口坐标。"""
    try:
        host, _ = await _vtable_host(ctx, session)
        result = await vt.get_cell_center(host, col_field, row_index)
        return {"ok": True, **result}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@tool(
    title="VTable: Scroll To",
    description="滚动 VTable 至目标位置（实例 API scrollToCol/scrollToRow/scrollToCell/setScrollLeft/setScrollTop）。四种用法：1) col_field+row_index 滚到指定单元格；2) 仅 col_field 横向滚到该列；3) 仅 row_index 纵向滚到该行；4) scroll_left/scroll_top 直接设偏移。verify=True（默认）滚动后校验目标进入可视区，返回最新 scrollLeft/scrollTop。滚动后配合 vtable_get_cell_center 取最新坐标再点击。",
    icons=[_VTABLE_ICON],
    tags={"vtable", "browser", "qa"},
)
async def vtable_scroll_to(
    ctx: Context,
    col_field: int | str | None = None,
    row_index: int | None = None,
    scroll_left: int | float | None = None,
    scroll_top: int | float | None = None,
    session: str | None = None,
    verify: bool = True,
) -> dict:
    """滚动 VTable 到目标位置。"""
    try:
        host, _ = await _vtable_host(ctx, session)
        result = await vt.scroll_to(
            host, col_field, row_index, scroll_left, scroll_top, verify
        )
        return {"ok": True, **result}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@tool(
    title="VTable: Select Rows",
    description="勾选/取消勾选 VTable 行（canvas 渲染，DOM 无复选框；内部完成实例刷新、checkbox 列定位、坐标合成并发送真实鼠标点击）。row_indexes 为 0 起始纯数据行索引列表；action 可选 check（默认，幂等）/uncheck/toggle。返回勾选前后变化。",
    icons=[_VTABLE_ICON],
    tags={"vtable", "browser", "qa"},
)
async def vtable_select_rows(
    ctx: Context,
    row_indexes: list[int],
    session: str | None = None,
    action: str = "check",
) -> dict:
    """通过真实鼠标点击 VTable canvas 上的复选框勾选/取消勾选指定行。"""
    try:
        host, page = await _vtable_host(ctx, session)
        result = await vt.select_rows(host, page, row_indexes, action)
        return {"ok": True, **result}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@tool(
    title="VTable: Drag Column",
    description="真实鼠标拖拽：把 source 列拖到 target 列前方(before)/后方(after)。先点击源列头中部选中整列（表头未启用整列选中时自动兜底纵向框选整列），再分步拖拽到落点列松开；不使用实例 API 改列位置。source/target 支持列索引或字段名/列标题。返回拖拽前后列顺序、验证结果；未开启列头拖拽（dragHeaderMode）或列级 dragHeader=false 时给出明确报错。",
    icons=[_VTABLE_ICON],
    tags={"vtable", "browser", "qa"},
)
async def vtable_drag_column(
    ctx: Context,
    source: int | str,
    target: int | str,
    position: str = "after",
    session: str | None = None,
) -> dict:
    """通过真实鼠标拖拽 VTable 列头换位。"""
    try:
        host, page = await _vtable_host(ctx, session)
        result = await vt.drag_column(host, page, source, target, position)
        return {"ok": True, **result}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@tool(
    title="VTable: Resize Column",
    description="真实鼠标拖拽：把 col 列宽调整到指定像素值 width。采集列头右边界分隔线位置合成顶层视口坐标，分步拖到目标位置后松开；不使用实例 API 改列宽。col 支持列索引或字段名/列标题。拖拽后重读列宽校验（误差≤2px）；未开启 columnResize 或超出 min/max 边界时给出明确报错。",
    icons=[_VTABLE_ICON],
    tags={"vtable", "browser", "qa"},
)
async def vtable_resize_column(
    ctx: Context,
    col: int | str,
    width: int,
    session: str | None = None,
) -> dict:
    """通过真实鼠标拖拽 VTable 列头分隔线调整列宽。"""
    try:
        host, page = await _vtable_host(ctx, session)
        result = await vt.resize_column(host, page, col, width)
        return {"ok": True, **result}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)

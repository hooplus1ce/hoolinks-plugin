"""VTable (canvas 渲染表格) 自动化基础设施。

对齐 qa-automation-plugin 的 tools/vtable.py，适配本扁平项目：
- 实例发现 / JS 注入 / 单元格与列头坐标计算 / 记录读取均在此层；
- host = 持有 VTable 的 frame 或顶层 page（由上层 components.tools.vtable 解析后传入，
  两者均提供 .evaluate / .frame_element 等 Playwright API）；
- 鼠标类操作 (select_rows / drag_column / resize_column) 需要同时传入
  host (JS 执行目标) 与 page (顶层页面, 用于 page.mouse 真实鼠标事件)；
- 所有几何坐标均为【顶层视口坐标】，可直接传给 page.mouse.click(x, y)。

vtable-scanner.js: mountVTable / scanColumns / scanHeaderCellIcons (表头图标坐标)
vtable-column-values.js: getColumnValuesByTitle / getCellRenderInfo / getCellCenterViewport /
  scrollToCell / getHeaderDragGeometry / getHeaderResizeGeometry 等。
两脚本均通过 window.frameElement.getBoundingClientRect() 一次性算出顶层视口坐标，
Python 侧不再叠加 iframe 偏移（select_rows 例外，其坐标合成在 Python 侧完成）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from pathlib import Path
from typing import Any, Callable, List, Union

logger = logging.getLogger("qa_mcp.vtable")

_JS_DIR = Path(__file__).parent
_SCANNER_JS = (_JS_DIR / "vtable_scanner.js").read_text(encoding="utf-8")
_VALUES_JS = (_JS_DIR / "vtable_column_values.js").read_text(encoding="utf-8")


# ---- 几何数据防御 (虚拟滚动哨兵值) ----
# VTable 对未渲染的虚拟滚动单元格, scenegraph 会返回 ±Number.MAX_VALUE 哨兵 bounds,
# 经 evaluate 序列化后 Python 侧可能拿到 float('inf') / float('nan')。
# 统一在拿到几何结果后净化: 非有限浮点 → None, 避免后续 int()/坐标求和直接崩溃。
def _finite_num(v, default=None) -> Union[float, None]:
    """若 v 为有限数字返回 float(v), 否则返回 default (None 表示缺失/无效)。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _sanitize_geom(geom):
    """把几何结果中的非有限浮点 (NaN/Infinity) 递归归一化为 None。"""
    if isinstance(geom, dict):
        return {k: _sanitize_geom(v) for k, v in geom.items()}
    if isinstance(geom, list):
        return [_sanitize_geom(v) for v in geom]
    if isinstance(geom, float) and not math.isfinite(geom):
        return None
    return geom


def _rect_coords_ok(rect) -> bool:
    """列头/落点矩形四角坐标均需为有限数字, 否则视为无效几何。"""
    if not isinstance(rect, dict):
        return False
    return all(_finite_num(rect.get(k)) is not None for k in ("x1", "x2", "y1", "y2"))


def _point_ok(center) -> bool:
    """点坐标 (x/y) 需为有限数字。"""
    return bool(
        isinstance(center, dict)
        and _finite_num(center.get("x")) is not None
        and _finite_num(center.get("y")) is not None
    )


async def _run_vtable_js(host, call_expr: str) -> Any:
    """在目标 host (frame 或顶层 page) 中执行 挂载 + 调用表达式, 返回其结果。"""
    script = _SCANNER_JS + "\n" + _VALUES_JS + "\n" + call_expr
    return await host.evaluate(script)


async def _poll_vtable(
    host,
    call_expr_fn: Callable[[], str],
    predicate: Callable[[Any], bool],
    timeout_ms: float = 3000,
    interval_ms: float = 0.15,
) -> Any:
    """轮询 VTable JS 结果直到谓词成立或超时, 返回最后一次结果。

    canvas 渲染无 DOM 信号, Playwright 无法被动等待; 用"就绪即继续"的
    状态轮询替代固定 sleep —— 条件通常 1~2 轮即命中, 比固定延时更高效。
    """
    deadline = time.monotonic() + timeout_ms / 1000
    last = None
    while True:
        last = await _run_vtable_js(host, call_expr_fn())
        if predicate(last):
            return last
        if time.monotonic() >= deadline:
            return last
        await asyncio.sleep(interval_ms)


async def _iframe_offset(host) -> dict:
    """返回持有 VTable 的 iframe 相对顶层视口的偏移；host 为顶层 page 时返回 0。

    等价于 plugin 的 page.locator(iframe_selector).bounding_box()，但无需 selector：
    直接对 host (Frame) 调用 frame_element().bounding_box()（Playwright 原生，
    返回相对主 frame 的坐标）。
    """
    if hasattr(host, "frame_element"):
        try:
            el = await host.frame_element()
            box = await el.bounding_box()
            if box:
                return {"left": float(box["x"]), "top": float(box["y"])}
        except Exception:  # noqa: BLE001
            pass
    return {"left": 0.0, "top": 0.0}


async def refresh_instance(host) -> dict:
    """在指定 host 下寻址并刷新最新 vtable 实例至 window._vtable。

    使用 vtable_scanner.js 的 mountVTable (含可见性检查 + React fiber 向上回溯)。
    """
    result = await _run_vtable_js(host, """
    (() => {
        const m = mountVTable();
        if (!m.ok) return { error: m.reason };
        return { status: "success", levels: m.levels };
    })()
    """)
    if result.get("error"):
        raise RuntimeError(f"挂载 VTable 实例失败: {result.get('error')}")
    return {
        "status": "success",
        "message": "最新 VTable 实例已成功抓取并挂载到 window._vtable",
    }


async def analyze_headers(
    host, max_col: int = 200, sample_rows: int = 2
) -> List[dict]:
    """场景图驱动分析列头与单元格内的交互图标组件。

    从 VTable 场景图 (scenegraph) 渲染层收集真实渲染的图标节点:
      - header_icons: 表头行交互图标 (排序/筛选/下拉/冻结/checkbox 等), 含顶层视口坐标;
      - cell_icons: 前 sample_rows 个已渲染 body 单元格内的交互图标组件。
    """
    await refresh_instance(host)
    result = await _run_vtable_js(host, f"""
    (() => {{
        const m = mountVTable();
        if (!m.ok) return {{ error: m.reason }};
        const cols = scanHeaderCellIcons({int(max_col)}, {int(sample_rows)});
        if (!cols) return {{ error: 'scanHeaderCellIcons 返回空' }};
        return {{ ok: true, columns: cols }};
    }})()
    """)
    if result.get("error"):
        raise RuntimeError(f"分析 VTable 列头失败: {result.get('error')}")

    columns = []
    for c in result.get("columns", []):
        header_icons = c.get("headerIcons", [])
        cell_icons = c.get("cellIcons", [])
        funcs = {ic.get("func", "") for ic in header_icons}
        funcs.discard("")
        capabilities = {
            "sortable": any("排序" in f for f in funcs),
            "filterable": any("筛选" in f for f in funcs),
            "hasCustomIcon": len(header_icons) > 0,
            "interactiveCell": len(cell_icons) > 0,
            "cellIconCount": len(cell_icons),
        }
        columns.append({
            "col": c.get("col", 0),
            "field": c.get("field", ""),
            "title": c.get("title", ""),
            "isFrozen": c.get("isFrozen", False),
            "header_icons": header_icons,
            "cell_icons": cell_icons,
            "capabilities": capabilities,
        })
    return columns


async def scan_columns(host, max_col: int = 200) -> List[dict]:
    """扫描 VTable 全部列 (含多级表头): 标题、body 行为分类、表头图标顶层视口坐标。"""
    await refresh_instance(host)
    result = await _run_vtable_js(host, f"""
    (() => {{
        const m = mountVTable();
        if (!m.ok) return {{ error: m.reason }};
        const cols = scanColumns({int(max_col)});
        if (!cols) return {{ error: "scanColumns 返回空" }};
        return {{ ok: true, columns: cols }};
    }})()
    """)
    if result.get("error"):
        raise RuntimeError(f"扫描 VTable 列失败: {result.get('error')}")
    return result.get("columns", [])


async def get_column_values(host, titles: List[str], raw: bool = False) -> dict:
    """按中文列标题读取该列所有单元格的值。

    raw=false: 读取场景图渲染后的视觉文本 (与界面显示一致);
    raw=true: 读取原始字段值 (如数值/状态码)。
    """
    await refresh_instance(host)
    titles_json = json.dumps(titles, ensure_ascii=False)
    result = await _run_vtable_js(host, f"""
    (() => {{
        const m = mountVTable();
        if (!m.ok) return {{ error: m.reason }};
        return getColumnsValuesByTitle(window._vtable, {titles_json}, {json.dumps(bool(raw))});
    }})()
    """)
    if result.get("error"):
        raise RuntimeError(f"读取列值失败: {result.get('error')}")
    return result


async def get_cell_render_info(
    host, col_field: Union[int, str], row_index: int, detail: str = "basic"
) -> dict:
    """读取某个单元格的场景图渲染信息 (视觉文本/颜色/边框/字体等)。"""
    await refresh_instance(host)
    col_json = json.dumps(col_field)
    result = await _run_vtable_js(host, f"""
    (() => {{
        const m = mountVTable();
        if (!m.ok) return {{ error: m.reason }};
        const t = window._vtable;
        let colIdx = null;
        if (typeof {col_json} === 'number') {{
            colIdx = {col_json};
        }} else {{
            const cols = t.columns || (t.options && t.options.columns) || [];
            const found = cols.findIndex(c => c.field === {col_json} || c.title === {col_json});
            if (found === -1) return {{ error: '未找到列: ' + {col_json} }};
            colIdx = found;
        }}
        const bodyRow = {int(row_index)} + (t.columnHeaderLevelCount || 1);
        return getCellRenderInfo(colIdx, bodyRow, {json.dumps(detail)});
    }})()
    """)
    if result.get("error"):
        raise RuntimeError(f"读取单元格渲染信息失败: {result.get('error')}")
    return result


async def get_cell_center(host, col_field: Union[int, str], row_index: int) -> dict:
    """读取单元格中心的【顶层视口坐标】viewportX/viewportY。

    坐标由 vtable_column_values.js 经场景图 globalAABBBounds 计算,
    可直接作为 page.mouse.click(x, y) 的点击坐标。
    """
    await refresh_instance(host)
    col_json = json.dumps(col_field)
    result = await _run_vtable_js(host, f"""
    (() => {{
        const m = mountVTable();
        if (!m.ok) return {{ error: m.reason }};
        const t = window._vtable;
        let colIdx = null;
        if (typeof {col_json} === 'number') {{
            colIdx = {col_json};
        }} else {{
            const cols = t.columns || (t.options && t.options.columns) || [];
            const found = cols.findIndex(c => c.field === {col_json} || c.title === {col_json});
            if (found === -1) return {{ error: '未找到列: ' + {col_json} }};
            colIdx = found;
        }}
        const bodyRow = {int(row_index)} + (t.columnHeaderLevelCount || 1);
        const pt = getCellCenterViewport(colIdx, bodyRow);
        if (!pt) return {{ error: '无法计算单元格中心坐标 (该行可能未渲染, 尝试 scrollToCell 后重试)' }};
        return {{ ok: true, viewportX: pt.viewportX, viewportY: pt.viewportY, col: colIdx, bodyRow: bodyRow }};
    }})()
    """)
    if result.get("error"):
        raise RuntimeError(f"读取单元格中心坐标失败: {result.get('error')}")
    return result


async def scroll_to(
    host,
    col_field: Union[int, str, None] = None,
    row_index: Union[int, None] = None,
    scroll_left: Union[int, float, None] = None,
    scroll_top: Union[int, float, None] = None,
    verify: bool = True,
) -> dict:
    """滚动 VTable 到目标位置 (等价于拖动横/纵向滚动条滑块)。

    三种用法 (按优先级):
      1) col_field + row_index: 滚动到指定单元格 (scrollToCell)
      2) 仅 col_field:          横向滚动到指定列 (scrollToCol)
      3) 仅 row_index:          纵向滚动到指定行 (scrollToRow)
      4) scroll_left/scroll_top: 直接设置滚动偏移 (setScrollLeft / setScrollTop)
    """
    if all(v is None for v in (col_field, row_index, scroll_left, scroll_top)):
        raise RuntimeError("至少需要提供 col_field / row_index / scroll_left / scroll_top 之一")

    await refresh_instance(host)

    # 解析列索引
    col_idx = None
    if col_field is not None:
        if isinstance(col_field, int):
            col_idx = col_field
        else:
            col_json = json.dumps(col_field)
            col_idx = await _run_vtable_js(host, f"""
            (() => {{
                const t = window._vtable;
                if (!t) return {{ error: 'no vtable' }};
                const cols = t.columns || (t.options && t.options.columns) || [];
                const found = cols.findIndex(c => c.field === {col_json} || c.title === {col_json});
                if (found === -1) return {{ error: '未找到列: ' + {col_json} }};
                return {{ col: found }};
            }})()
            """)
            if col_idx.get("error"):
                raise RuntimeError(f"解析列索引失败: {col_idx.get('error')}")
            col_idx = col_idx["col"]

    # 构造并执行滚动调用
    if col_idx is not None and row_index is not None:
        call_expr = f"scrollToCellPosition({int(col_idx)}, {int(row_index)})"
    elif col_idx is not None:
        call_expr = f"scrollToColumnByIndex({int(col_idx)})"
    elif row_index is not None:
        call_expr = f"scrollToRowByIndex({int(row_index)})"
    else:
        sl = "null" if scroll_left is None else json.dumps(float(scroll_left))
        st = "null" if scroll_top is None else json.dumps(float(scroll_top))
        call_expr = f"setScrollPosition({sl}, {st})"

    result = await _run_vtable_js(host, f"""
    (() => {{
        const t = window._vtable;
        if (!t) return {{ error: 'window._vtable 未准备好' }};
        const r = {call_expr};
        return r;
    }})()
    """)
    if result.get("error"):
        raise RuntimeError(f"滚动 VTable 失败: {result.get('error')}")
    if not result.get("ok"):
        raise RuntimeError(f"滚动 VTable 失败: {result.get('reason')}")

    if verify and col_idx is not None:
        body_row = int(row_index) + 1 if row_index is not None else 1
        await _poll_vtable(
            host,
            lambda: f"({{ const v = isCellInViewport({int(col_idx)}, {int(body_row)}); return {{ visible: v }}; }})()",
            lambda r: bool(r.get("visible")),
        )
    else:
        await asyncio.sleep(0.15)

    state = await _run_vtable_js(host, """
    (() => {
        const s = getScrollStateInfo();
        if (!s) return { error: 'no scroll state' };
        return s;
    })()
    """)

    resp: dict = {
        "status": "success",
        "api": result.get("api", ""),
        "target": {
            "col": col_idx,
            "col_field": col_field,
            "row_index": row_index,
            "scroll_left": scroll_left,
            "scroll_top": scroll_top,
        },
        "scroll": state,
    }

    if verify and col_idx is not None:
        body_row = int(row_index) + 1 if row_index is not None else 1
        visible = await _run_vtable_js(host, f"""
        (() => {{
            const v = isCellInViewport({int(col_idx)}, {int(body_row)});
            return {{ visible: v }};
        }})()
        """)
        resp["verification"] = {
            "ok": bool(visible.get("visible")),
            "cell_in_viewport": bool(visible.get("visible")),
        }
        if not visible.get("visible"):
            resp["verification"]["note"] = (
                "目标单元格未完全落入可视区，可再调用 vtable_get_cell_center "
                "获取最新坐标后点击"
            )
    return resp


async def get_row_count(host) -> int:
    """读取当前表格有多少行。"""
    await refresh_instance(host)
    get_count_js = """
    () => {
        if (!window._vtable) return { error: "window._vtable 未准备好" };
        const vtable = window._vtable;
        const columns = vtable.columns || (vtable.options && vtable.options.columns) || [];
        for (let col of columns) {
            if (col.vtable_aggregator && col.vtable_aggregator.records) {
                return { status: "success", count: col.vtable_aggregator.records.length };
            }
        }
        const rowCount = vtable.rowCount || 0;
        const headerRowCount = vtable.columnHeaderLevelCount || 1;
        return { status: "success", count: Math.max(0, rowCount - headerRowCount) };
    }
    """
    result = await host.evaluate(get_count_js)
    if result.get("error"):
        raise RuntimeError(result.get("error"))
    return result.get("count", 0)


async def get_all_records(host) -> List[dict]:
    """一次性读取表格所有的后台完整记录对象。

    优先从 vtable_aggregator.records 读取；若列配置中不存在该结构 (普通 VTable 数据表)，
    则回退读取 vtable.records。
    """
    await refresh_instance(host)
    get_records_js = """
    () => {
        if (!window._vtable) return { error: "window._vtable 未准备好" };
        const vtable = window._vtable;
        const columns = vtable.columns || (vtable.options && vtable.options.columns) || [];
        for (let col of columns) {
            if (col.vtable_aggregator && col.vtable_aggregator.records) {
                return { status: "success", records: col.vtable_aggregator.records };
            }
        }
        const records = vtable.records;
        if (Array.isArray(records) && records.length > 0) {
            return { status: "success", records: records };
        }
        return { error: "未能在 vtable 中读取到 records" };
    }
    """
    result = await host.evaluate(get_records_js)
    if result.get("error"):
        raise RuntimeError(result.get("error"))
    return result.get("records", [])


async def get_cell_text(
    host, row_index: int, col_field: str, visual: bool = True
) -> Any:
    """读取某个具体单元格的值。

    row_index: 行号 (0-indexed，纯数据行，不含表头)
    col_field: 列的 field 名称 或 列标题
    visual: True(默认)= 读取场景图渲染层文本 (与界面显示一致);
            False = 读取数据源 records 原始值 (忽略排序/筛选)
    """
    if visual:
        info = await get_cell_render_info(host, col_field, row_index)
        if info.get("ok"):
            return info.get("text", info.get("value"))
        if info.get("reason") == "cell not rendered":
            raise RuntimeError(
                f"第 {row_index} 行不在渲染视口内 (虚拟滚动), 无法读取界面文本。"
                f"请先滚动到该行, 或使用 visual=False 读取数据源, "
                f"或使用 vtable_get_column_values / vtable_get_all_records。"
            )
        raise RuntimeError(f"读取单元格渲染信息失败: {info.get('reason')}")

    records = await get_all_records(host)
    if row_index < 0 or row_index >= len(records):
        raise RuntimeError(f"行索引 {row_index} 越界，当前共 {len(records)} 行。")
    record = records[row_index]
    return record.get(col_field, None)


async def _get_checked_keys(host) -> List[str]:
    """读取当前已勾选的 checkbox 行 key 列表 (来自 VTable stateManager.checkedState)。"""
    return await host.evaluate("""() => {
        const vtable = window._vtable;
        if (!vtable || !vtable.stateManager || !vtable.stateManager.checkedState) return [];
        const cs = vtable.stateManager.checkedState;
        const out = [];
        if (typeof cs.forEach === 'function') {
            cs.forEach((val, key) => {
                if (val && val._vtable_checkbox === true) out.push(String(key));
            });
        }
        return out;
    }""")


async def _is_checked(host, record_index: int) -> bool:
    """判断指定行当前是否处于勾选状态。"""
    keys = await _get_checked_keys(host)
    return str(record_index) in keys


async def select_rows(
    host, page, row_indexes: List[int], action: str = "check"
) -> dict:
    """通过真实鼠标点击 VTable canvas 上的复选框来勾选/取消勾选指定行。

    坐标合成在 Python 侧完成: iframe 相对顶层偏移 (frame_element.bounding_box) +
    canvas 相对 iframe 偏移 (iframe 内 canvas.getBoundingClientRect) + 单元格中心,
    得到顶层视口坐标后由 page.mouse.click 真实命中 canvas 复选框。
    """
    if not row_indexes:
        raise RuntimeError("row_indexes 不能为空")
    if action not in ("check", "uncheck", "toggle"):
        raise RuntimeError(f"action 仅支持 check / uncheck / toggle，收到: {action}")

    await refresh_instance(host)

    locate_js = f"""
    () => {{
        const vtable = window._vtable;
        if (!vtable) return {{ error: 'window._vtable 未准备好' }};
        const columns = vtable.columns || (vtable.options && vtable.options.columns) || [];
        let colIdx = columns.findIndex(c =>
            c.field === '_vtable_checkbox' || c.cellType === 'checkbox' || c.headerType === 'checkbox'
        );
        if (colIdx === -1) return {{ error: '未找到复选框列' }};

        const canvas = vtable.container ? vtable.container.querySelector('canvas') : null;
        if (!canvas) return {{ error: '未找到 VTable canvas' }};
        const cr = canvas.getBoundingClientRect();
        const canvasH = cr.height;
        // getCellRect 返回的是表格绝对内容坐标, 需减去滚动偏移才是 canvas 可视区坐标
        const scrollTop = vtable.scrollTop || 0;
        const scrollLeft = vtable.scrollLeft || 0;

        const targets = {json.dumps(row_indexes)};
        const rects = [];
        for (const ri of targets) {{
            let bodyRow = null;
            try {{ bodyRow = vtable.getRecordStartRowByRecordIndex(ri); }} catch (e) {{ bodyRow = null; }}
            if (bodyRow == null) bodyRow = ri + (vtable.columnHeaderLevelCount || 1);
            const rect = vtable.getCellRect(colIdx, bodyRow);
            if (!rect || !rect.bounds) return {{ error: `无法获取第 ${{ri}} 行单元格矩形` }};
            const vx1 = rect.bounds.x1 - scrollLeft;
            const vx2 = rect.bounds.x2 - scrollLeft;
            const vy1 = rect.bounds.y1 - scrollTop;
            const vy2 = rect.bounds.y2 - scrollTop;
            rects.push({{
                record_index: ri,
                body_row: bodyRow,
                rect: {{ x1: vx1, y1: vy1, x2: vx2, y2: vy2 }},
                visible: vy1 >= 0 && vy2 <= canvasH
            }});
        }}
        return {{
            col_index: colIdx,
            canvas_rect: {{ left: cr.left, top: cr.top }},
            targets: rects
        }};
    }}
    """
    located = await host.evaluate(locate_js)
    if located.get("error"):
        raise RuntimeError(located["error"])

    # 顶层文档上下文：计算 iframe 相较于顶层视口的偏移 (Playwright 原生, 免手写 JS)
    iframe_rect = await _iframe_offset(host)

    # 根据 action 决定每行是否需要点击（check/uncheck 幂等，toggle 全点）
    checked_keys = set(await _get_checked_keys(host))
    to_click = []
    for t in located["targets"]:
        key = str(t["record_index"])
        is_checked = key in checked_keys
        if action == "check" and not is_checked:
            to_click.append(t)
        elif action == "uncheck" and is_checked:
            to_click.append(t)
        elif action == "toggle":
            to_click.append(t)

    # 逐行处理: 每行点击前确保其位于可视区 (一次滚动 + 重新定位只针对当前行)
    clicked = []
    for t in to_click:
        if action == "check":
            expected = True
        elif action == "uncheck":
            expected = False
        else:
            expected = not (str(t["record_index"]) in checked_keys)

        for attempt in range(3):
            if not t["visible"]:
                await host.evaluate("""(args) => {
                    const vtable = window._vtable;
                    if (typeof vtable.scrollToCell === 'function') {
                        vtable.scrollToCell({ row: args.bodyRow, col: args.colIndex });
                    }
                    return true;
                }""", {"bodyRow": t["body_row"], "colIndex": located["col_index"]})
                # 轮询滚动后该行进入可视区 (canvas 无 DOM 信号, 状态轮询替代固定 sleep)
                current = None
                for _ in range(20):
                    current = await host.evaluate("""(args) => {
                        const vtable = window._vtable;
                        const rect = vtable.getCellRect(args.colIndex, args.bodyRow);
                        const cr = vtable.container.querySelector('canvas').getBoundingClientRect();
                        const scrollTop = vtable.scrollTop || 0;
                        const scrollLeft = vtable.scrollLeft || 0;
                        const vy1 = rect.bounds.y1 - scrollTop;
                        const vy2 = rect.bounds.y2 - scrollTop;
                        return {
                            rect: { x1: rect.bounds.x1 - scrollLeft, y1: vy1, x2: rect.bounds.x2 - scrollLeft, y2: vy2 },
                            canvas_rect: { left: cr.left, top: cr.top },
                            visible: vy1 >= 0 && vy2 <= cr.height
                        };
                    }""", {"bodyRow": t["body_row"], "colIndex": located["col_index"]})
                    if current.get("visible"):
                        break
                    await asyncio.sleep(0.1)
                t["rect"] = current["rect"]
                located["canvas_rect"] = current["canvas_rect"]
                t["visible"] = current["visible"]

            # 两次偏移合成页面级坐标 (iframe 相对顶层 + canvas 相对 iframe), 发送真实鼠标点击
            cx = iframe_rect["left"] + located["canvas_rect"]["left"] + (t["rect"]["x1"] + t["rect"]["x2"]) / 2
            cy = iframe_rect["top"] + located["canvas_rect"]["top"] + (t["rect"]["y1"] + t["rect"]["y2"]) / 2
            logger.info(
                f"[select_rows] click row={t['record_index']} attempt={attempt + 1} "
                f"visible={t['visible']} coord=({cx:.1f}, {cy:.1f})"
            )
            await page.mouse.click(cx, cy)

            # 点击后验证: 轮询勾选状态直至预期 (canvas 无 DOM 信号, 状态轮询替代固定 sleep)
            checked_ok = False
            for _ in range(20):
                if await _is_checked(host, t["record_index"]) == expected:
                    checked_ok = True
                    break
                await asyncio.sleep(0.15)
            if checked_ok:
                break

        clicked.append({"record_index": t["record_index"], "body_row": t["body_row"]})

    # 轮询勾选状态直至稳定
    checked_after = await _get_checked_keys(host)
    for _ in range(6):
        if checked_after != sorted(checked_keys) or not clicked:
            break
        await asyncio.sleep(0.25)
        checked_after = await _get_checked_keys(host)

    return {
        "action": action,
        "clicked": clicked,
        "checked_before": sorted(checked_keys),
        "checked_after": sorted(checked_after),
        "added": sorted(set(checked_after) - checked_keys),
        "removed": sorted(checked_keys - set(checked_after)),
    }


async def drag_column(
    host, page, source: Union[int, str], target: Union[int, str], position: str = "after"
) -> dict:
    """通过真实鼠标拖拽 VTable 列头，把 source 列移动到 target 列的前方/后方。"""
    pos = (position or "after").lower()
    if pos not in ("before", "after"):
        raise RuntimeError(f"position 仅支持 before / after，收到: {position}")

    await refresh_instance(host)

    # ---- 1. 解析源列/目标列索引 (当前可见顺序) ----
    resolve_js = f"""
    () => {{
        const t = window._vtable;
        if (!t) return {{ error: 'window._vtable 未准备好' }};
        const headerRow = Math.max((t.columnHeaderLevelCount || 1) - 1, 0);
        const fieldOf = (c) => {{ try {{ const f = t.getHeaderField ? t.getHeaderField(c, headerRow) : null; return f === null || f === undefined ? '' : String(f); }} catch (e) {{ return ''; }} }};
        const titleOf = (c) => {{ let s = ''; try {{ const v = t.getCellValue ? t.getCellValue(c, headerRow) : null; if (v !== null && v !== undefined) s = v; }} catch (e) {{}} if (!s) {{ try {{ const d = t.getHeaderDefine ? t.getHeaderDefine(c, headerRow) : null; if (d) s = d.title || d.caption || ''; }} catch (e) {{}} }} return typeof s === 'string' ? s : String(s); }};
        const colCount = t.colCount || 0;
        const resolve = (ref) => {{
            if (typeof ref === 'number') {{
                return (ref >= 0 && ref < colCount) ? ref : {{ error: '列索引越界: ' + ref + ' (colCount=' + colCount + ')' }};
            }}
            const s = String(ref);
            for (let c = 0; c < colCount; c++) {{
                if (fieldOf(c) === s || titleOf(c) === s) return c;
            }}
            return {{ error: '未找到列: ' + s + ' (可尝试用列索引, 或先 vtable_scan_columns 查看实际列标题)' }};
        }};
        const src = resolve({json.dumps(source)});
        if (src && src.error) return src;
        const tgt = resolve({json.dumps(target)});
        if (tgt && tgt.error) return tgt;
        return {{ ok: true, sourceCol: src, targetCol: tgt, fieldOf: fieldOf(src), titleOf: titleOf(src), targetField: fieldOf(tgt), targetTitle: titleOf(tgt) }};
    }}
    """
    resolved = await host.evaluate(resolve_js)
    if resolved.get("error"):
        raise RuntimeError(resolved["error"])
    source_col = resolved["sourceCol"]
    target_col = resolved["targetCol"]

    # ---- 2. 计算落点列 (VTable 原生语义: 指针所在列决定前后) ----
    if pos == "before":
        drop_col = target_col if target_col < source_col else target_col - 1
    else:
        drop_col = target_col if target_col > source_col else target_col + 1
    if drop_col == source_col:
        return {
            "status": "noop",
            "reason": (
                f"源列 {resolved['fieldOf']} 已在目标列 {resolved['targetField']} 的"
                f"{('前方' if pos == 'before' else '后方')}, 无需拖拽"
            ),
            "source": {"col": source_col, "field": resolved["fieldOf"], "title": resolved["titleOf"]},
            "target": {"col": target_col, "field": resolved["targetField"], "title": resolved["targetTitle"]},
            "position": pos,
        }

    # ---- 3. 采集拖拽几何信息 (仅读) ----
    geom = await _run_vtable_js(host, f"getHeaderDragGeometry({int(source_col)}, {int(drop_col)})")
    if geom.get("error"):
        raise RuntimeError(f"采集列头几何信息失败: {geom['error']}")
    geom = _sanitize_geom(geom)

    drag_mode = geom.get("dragHeaderMode")
    if drag_mode not in ("all", "column"):
        raise RuntimeError(
            f"VTable 未开启列头拖拽: dragHeaderMode={drag_mode} "
            f"(需 'all' 或 'column', 前端需配置 dragHeaderMode 或 dragOrder.dragHeaderMode)"
        )
    if geom.get("sourceCanDragByDefine") is False and not geom.get("sourceIsFrozen"):
        try:
            define_ok = await host.evaluate(
                """(args) => {
                    const t = window._vtable;
                    try {
                        const d = t.getHeaderDefine ? t.getHeaderDefine(args.col, args.row) : null;
                        return d ? !(d.dragHeader === false) : true;
                    } catch (e) { return true; }
                }""",
                {"col": source_col, "row": geom["headerRow"]},
            )
        except Exception:
            define_ok = True
        if not define_ok:
            raise RuntimeError(f"源列 {resolved['fieldOf']} 配置了 dragHeader:false, 禁止拖拽换位")

    src_h = geom.get("sourceHeader")
    drop_h = geom.get("dropHeader")
    if not src_h or not _rect_coords_ok(src_h):
        raise RuntimeError(
            "无法获取源列表头矩形 (该列可能未渲染或坐标无效): "
            "请先横向滚动使源列可见后重试"
        )
    if not drop_h or not _rect_coords_ok(drop_h):
        raise RuntimeError(
            f"无法获取落点列 (col={drop_col}) 表头矩形: 该列可能未渲染 (虚拟滚动), "
            f"请先横向滚动使源列与目标列同时可见后重试"
        )
    if not src_h.get("visible"):
        raise RuntimeError("源列表头当前不可见 (横向视口外), 请先横向滚动使源列可见后重试")
    if not drop_h.get("visible"):
        raise RuntimeError(
            f"落点列 (col={drop_col}) 表头当前不可见 (横向视口外), "
            f"请先横向滚动使源列与目标列同时可见后重试"
        )

    frozen_mode = geom.get("frozenColDragHeaderMode")
    if frozen_mode == "disabled":
        if geom.get("sourceIsFrozen"):
            raise RuntimeError(
                f"源列 {resolved['fieldOf']} 为冻结列且 frozenColDragHeaderMode=disabled, 禁止拖拽"
            )
        if geom.get("dropIsFrozen"):
            raise RuntimeError(
                f"落点列 (col={drop_col}) 为冻结列且 frozenColDragHeaderMode=disabled, 无法拖入冻结区"
            )

    src_cx = (src_h["x1"] + src_h["x2"]) / 2
    src_cy = (src_h["y1"] + src_h["y2"]) / 2
    drop_cx = (drop_h["x1"] + drop_h["x2"]) / 2
    drop_cy = (drop_h["y1"] + drop_h["y2"]) / 2

    # ---- 4. 真实交互: 点击列头中部选中整列 (VTable 拖拽启动前提) ----
    header_row_num = _finite_num(geom.get("headerRow"), 0)
    header_row_num = int(header_row_num)
    icon_info = await _run_vtable_js(
        host,
        f"getCellIconsViewport({int(source_col)}, {header_row_num}, '', 'basic')",
    )
    blockers = []
    for ic in (icon_info or {}).get("icons", []) or []:
        name = str(ic.get("name") or "")
        if name in ("content", "text", ""):
            continue  # 文本节点不拦截交互
        w, h = ic.get("width") or 0, ic.get("height") or 0
        if 0 < w <= 500 and 0 < h <= 500:
            blockers.append({"x": ic.get("viewportX"), "y": ic.get("viewportY"), "w": w, "h": h})

    def _icon_free_point() -> tuple:
        width = src_h["x2"] - src_h["x1"]
        py = (src_h["y1"] + src_h["y2"]) / 2
        best = None
        for frac in (0.5, 0.25, 0.75, 0.12, 0.88, 0.38, 0.62):
            px = src_h["x1"] + width * frac
            free = all(
                abs(px - b["x"]) > b["w"] / 2 + 6 or abs(py - b["y"]) > b["h"] / 2 + 6
                for b in blockers
            )
            if free:
                return px, py
            if best is None:
                best = (px, py)
        return best if best is not None else (src_cx, src_cy)

    click_points = [_icon_free_point()]
    if click_points[0] != (src_cx, src_cy):
        click_points.append((src_cx, src_cy))

    async def _select_source_column() -> tuple:
        for px, py in click_points:
            await page.mouse.click(px, py)
            for _ in range(24):
                sel = await _run_vtable_js(
                    host, f"getColumnSelectionState({int(source_col)})"
                )
                if isinstance(sel, dict) and sel.get("selected"):
                    return True, px, py
                await asyncio.sleep(0.1)
        return False, None, None

    async def _select_source_column_by_drag() -> tuple:
        last_row = geom.get("lastBodyRowGlobal")
        last_center = geom.get("sourceLastBodyCenter")
        if _finite_num(last_row) is None:
            return False, None, None
        last_row = int(_finite_num(last_row))
        if not _point_ok(last_center):
            last_center = None
        if not geom.get("sourceLastBodyVisible", True) or not last_center:
            try:
                await host.evaluate(
                    """(args) => {
                        const t = window._vtable;
                        if (!t || typeof t.scrollToRow !== 'function') return false;
                        t.scrollToRow(args.row);
                        return true;
                    }""",
                    {"row": int(last_row)},
                )
                last_center = None
                for _ in range(20):
                    geom2 = await _run_vtable_js(
                        host,
                        f"getHeaderDragGeometry({int(source_col)}, {int(drop_col)})",
                    )
                    geom2 = _sanitize_geom(geom2 or {})
                    last_center = (geom2 or {}).get("sourceLastBodyCenter")
                    if _point_ok(last_center):
                        break
                    last_center = None
                    await asyncio.sleep(0.15)
            except Exception:
                last_center = None
            if not last_center:
                return False, None, None
        for cx, cy in click_points:
            await page.mouse.move(cx, cy)
            await asyncio.sleep(0.08)
            await page.mouse.down()
            await asyncio.sleep(0.1)
            tx = _finite_num(last_center.get("x"), 0.0)
            ty = _finite_num(last_center.get("y"), 0.0)
            steps = max(8, min(32, int(abs(ty - cy) / 50) + 1))
            await page.mouse.move(tx, ty, steps=steps)
            await asyncio.sleep(0.15)
            await page.mouse.up()
            for _ in range(24):
                sel = await _run_vtable_js(
                    host, f"getColumnSelectionState({int(source_col)})"
                )
                if isinstance(sel, dict) and sel.get("selected"):
                    return True, cx, cy
                await asyncio.sleep(0.1)
        return False, None, None

    async def _select_source_column_programmatic() -> bool:
        try:
            await host.evaluate(
                """(args) => {
                    const t = window._vtable;
                    const sm = t && t.stateManager;
                    if (!sm) return { ok: false, reason: '无 stateManager' };
                    const lastRow = (t.rowCount || 1) - 1;
                    const range = { start: { col: args.col, row: 0 }, end: { col: args.col, row: lastRow } };
                    let method = '';
                    if (typeof t.selectCells === 'function') { t.selectCells({ range, add: false }); method = 'selectCells'; }
                    else if (typeof t.selectRanges === 'function') { t.selectRanges([range]); method = 'selectRanges'; }
                    else if (sm.select) { sm.select.ranges = [range]; method = 'stateManager'; }
                    else return { ok: false, reason: '无 selectCells/selectRanges/stateManager.select' };
                    return { ok: true, method };
                }""",
                {"col": int(source_col)},
            )
            for _ in range(20):
                sel = await _run_vtable_js(
                    host, f"getColumnSelectionState({int(source_col)})"
                )
                if isinstance(sel, dict) and sel.get("selected"):
                    return True
                await asyncio.sleep(0.1)
            return False
        except Exception as e:
            logger.warning(f"[drag_column] 编程式整列选中失败: {e}")
            return False

    selected, sel_x, sel_y = await _select_source_column()
    if not selected:
        selected, sel_x, sel_y = await _select_source_column()
    if not selected:
        selected, sel_x, sel_y = await _select_source_column_by_drag()
    if not selected:
        if await _select_source_column_programmatic():
            selected, sel_x, sel_y = True, None, None
        else:
            logger.warning(
                f"[drag_column] 源列 {resolved['fieldOf']} 未能进入整列选中状态 "
                f"(headerSelectMode={geom.get('headerSelectMode')}), 继续执行拖拽动作链, "
                f"是否生效由最终验证判定"
            )

    # 选中后再确认拖拽启动条件 (列级 dragHeader 配置)
    can_drag = await host.evaluate(
        """(args) => {
            const t = window._vtable;
            try { return !!(t._canDragHeaderPosition && t._canDragHeaderPosition(args.col, args.row)); }
            catch (e) { return false; }
        }""",
        {"col": source_col, "row": header_row_num},
    )
    if not can_drag:
        logger.warning(
            f"[drag_column] 源列 {resolved['fieldOf']} 未满足拖拽启动条件 "
            f"(dragHeaderMode={drag_mode}, headerSelectMode={geom.get('headerSelectMode')}, "
            f"frozenColDragHeaderMode={frozen_mode}), 继续执行拖拽动作链"
        )

    # ---- 5. 分步真实拖拽 (按下 → 缓动移动 → 松开) ----
    press_x = sel_x if sel_x is not None else src_cx
    press_y = sel_y if sel_y is not None else src_cy
    logger.info(
        f"[drag_column] src=col{source_col}({resolved['fieldOf']}) "
        f"target=col{target_col}({resolved['targetField']}) position={pos} "
        f"drop=col{drop_col} coord=({drop_cx:.1f}, {drop_cy:.1f}) press=({press_x:.1f}, {press_y:.1f})"
    )
    await page.mouse.move(press_x, press_y)
    await asyncio.sleep(0.08)
    await page.mouse.down()
    await asyncio.sleep(0.1)
    await page.mouse.move(drop_cx, drop_cy, steps=14)
    await asyncio.sleep(0.15)
    await page.mouse.up()

    after_geom = None
    for _ in range(10):
        after_geom = await _run_vtable_js(
            host,
            f"getHeaderDragGeometry({int(source_col)}, {int(drop_col)})",
        )
        after_geom = _sanitize_geom(after_geom or {})
        if (
            isinstance(after_geom.get("fields"), list)
            and after_geom["fields"] != geom.get("fields", [])
        ):
            break
        await asyncio.sleep(0.05)

    # ---- 6. 读取拖拽后的列顺序并验证 ----
    after = after_geom.get("fields")
    if not isinstance(after, list):
        raise RuntimeError(f"拖拽后读取列顺序失败: {after_geom}")
    fields_before = geom.get("fields", [])
    fields_after = after
    titles_after = after_geom.get("titles", [])
    src_field = resolved["fieldOf"]
    tgt_field = resolved["targetField"]
    src_new = fields_after.index(src_field) if src_field in fields_after else -1
    tgt_new = fields_after.index(tgt_field) if tgt_field in fields_after else -1
    src_old = fields_before.index(src_field) if src_field in fields_before else -1

    moved = fields_after != fields_before
    if pos == "before":
        ok = moved and src_new + 1 == tgt_new
    else:
        ok = moved and src_new == tgt_new + 1

    if not ok:
        return {
            "status": "not_effective",
            "reason": (
                f"列拖拽动作链已执行但列顺序未变化: 期望 {resolved['titleOf']} "
                f"在 {resolved['targetTitle']} 的{('前方' if pos == 'before' else '后方')} "
                f"(新位置 src={src_new}, target={tgt_new}, moved={moved}). "
                f"诊断: dragHeaderMode={drag_mode}, headerSelectMode={geom.get('headerSelectMode')}, "
                f"frozenColDragHeaderMode={frozen_mode}. "
                f"可能原因: 目标与源列跨分组/层级 (VTable 默认禁止跨父级移动), "
                f"整列选中未达成 (选中机制被禁用), 或前端 validateDragOrderOnEnd 拒绝了本次移动。"
            ),
            "source": {"col": source_col, "field": src_field, "title": resolved["titleOf"]},
            "target": {"col": target_col, "field": tgt_field, "title": resolved["targetTitle"]},
            "position": pos,
            "selection": {"selected_full_column": selected, "header_select_mode": geom.get("headerSelectMode")},
            "drag_options": {
                "dragHeaderMode": drag_mode,
                "frozenColDragHeaderMode": frozen_mode,
            },
            "verification": {
                "ok": False,
                "source_index_before": src_old,
                "source_index_after": src_new,
                "target_index_after": tgt_new,
            },
        }

    return {
        "status": "success",
        "source": {"col": source_col, "field": src_field, "title": resolved["titleOf"]},
        "target": {"col": target_col, "field": tgt_field, "title": resolved["targetTitle"]},
        "position": pos,
        "drop": {
            "col": drop_col,
            "field": fields_before[drop_col] if drop_col < len(fields_before) else None,
            "page_coords": {"x": round(drop_cx, 2), "y": round(drop_cy, 2)},
        },
        "selection": {"selected_full_column": True, "header_select_mode": geom.get("headerSelectMode")},
        "drag_options": {
            "dragHeaderMode": drag_mode,
            "frozenColDragHeaderMode": frozen_mode,
        },
        "verification": {
            "ok": True,
            "source_index_before": src_old,
            "source_index_after": src_new,
            "target_index_after": tgt_new,
        },
        "columns_before": [
            {"field": f, "title": t} for f, t in zip(fields_before, geom.get("titles", []))
        ],
        "columns_after": [
            {"field": f, "title": t} for f, t in zip(fields_after, titles_after)
        ],
    }


async def resize_column(host, page, col: Union[int, str], width: int) -> dict:
    """通过真实鼠标拖拽 VTable 列头分隔线，把指定列宽调整到目标像素值。"""
    if not width or int(width) <= 0:
        raise RuntimeError(f"width 必须为正数 (px), 收到: {width}")
    target_width = int(width)

    await refresh_instance(host)

    # ---- 1. 采集列头几何 + 能力开关 (仅读) ----
    geom = await _run_vtable_js(host, f"getHeaderResizeGeometry({json.dumps(col)})")
    if geom.get("error"):
        raise RuntimeError(geom["error"])
    geom = _sanitize_geom(geom)

    h = geom.get("header")
    if not h or not _rect_coords_ok(h):
        raise RuntimeError(
            "无法获取列头矩形 (该列可能未渲染或坐标无效): "
            "请先横向滚动使该列可见后重试"
        )
    if not h.get("visible"):
        raise RuntimeError(
            f"列头当前不可见 (横向视口外): col={geom['col']}({geom['field']}), "
            f"请先横向滚动使该列可见后重试"
        )

    resize_cfg = geom.get("resize") or {}
    if resize_cfg.get("resizeEnabled") is False:
        raise RuntimeError(
            f"VTable 未开启列宽调整: columnResize.resizable=false "
            f"(resize 配置={resize_cfg.get('resize')}), 无法拖拽调整列宽"
        )
    min_w = resize_cfg.get("minColumnWidth")
    max_w = resize_cfg.get("maxColumnWidth")
    if isinstance(min_w, (int, float)) and target_width < min_w:
        raise RuntimeError(f"目标宽度 {target_width}px 小于配置的最小列宽 {min_w}px")
    if isinstance(max_w, (int, float)) and target_width > max_w:
        raise RuntimeError(f"目标宽度 {target_width}px 大于配置的最大列宽 {max_w}px")

    cur_width = _finite_num(h.get("width"), 0.0)
    start_x = _finite_num(h.get("x2"), 0.0)  # 分隔线 = 列头右边界
    start_y = (_finite_num(h.get("y1"), 0.0) + _finite_num(h.get("y2"), 0.0)) / 2
    target_x = _finite_num(h.get("x1"), 0.0) + target_width
    delta = target_x - start_x

    # ---- 2. 真实交互: 悬停分隔线 → 按下 → 分步缓动拖动 → 松开 ----
    logger.info(
        f"[resize_column] col={geom['col']}({geom['field']}) "
        f"width {cur_width}px -> {target_width}px "
        f"drag ({start_x:.1f}, {start_y:.1f}) -> ({target_x:.1f}, {start_y:.1f})"
    )
    await page.mouse.move(start_x, start_y)
    await asyncio.sleep(0.12)  # 悬停稳定, 让 VTable 进入 resize 判定区
    await page.mouse.down()
    await asyncio.sleep(0.12)
    await page.mouse.move(target_x, start_y, steps=18)
    await asyncio.sleep(0.15)  # 落点悬停稳定 (VTable 渲染拖拽反馈)
    await page.mouse.up()

    # 轮询列宽直至接近目标 (canvas 无 DOM 信号, 状态轮询替代固定 sleep)
    after = None
    for _ in range(10):
        after = await _run_vtable_js(host, f"getColumnWidth({json.dumps(geom['col'])})")
        w = (after or {}).get("width") if isinstance(after, dict) else None
        if w is not None and abs(w - target_width) <= 2:
            break
        await asyncio.sleep(0.05)

    # ---- 3. 重读列宽验证 ----
    after_w = (after or {}).get("width") if isinstance(after, dict) else None
    if after_w is None:
        raise RuntimeError(f"拖拽后读取列宽失败: {after}")
    ok = abs(after_w - target_width) <= 2
    return {
        "status": "success" if ok else "partial",
        "col": geom["col"],
        "field": geom["field"],
        "title": geom["title"],
        "width_before": cur_width,
        "width_target": target_width,
        "width_after": after_w,
        "delta": round(delta, 1),
        "drag_points": {
            "start": {"x": round(start_x, 2), "y": round(start_y, 2)},
            "end": {"x": round(target_x, 2), "y": round(start_y, 2)},
        },
        "resize_config": {
            "columnResize": resize_cfg.get("columnResize"),
            "resize": resize_cfg.get("resize"),
            "columnResizeMode": resize_cfg.get("columnResizeMode"),
        },
        "verified": ok,
        "message": (
            f"列 [{geom['title']}] 列宽 {cur_width}px → {after_w}px "
            f"(目标 {target_width}px): "
            f"{'已生效' if ok else '未完全命中, 请检查 columnResize 配置/该列是否可调整'}"
        ),
    }

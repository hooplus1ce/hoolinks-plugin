"""页面弹层/浮层/弹窗/消息检测 MCP 工具。

检测当前页面（顶层 DOM + 激活 iframe）中所有弹层类容器：
- antd portal 弹层：modal（.ant-modal-wrap）、drawer（.ant-drawer）、
  select 下拉（.ant-select-dropdown）、菜单下拉（.ant-dropdown）、popover/tooltip、
  message 通知、notification、cascader 下拉、日期/选择器下拉
- VTable 自绘弹层（canvas 同级兄弟节点或 body 下）：
  .vtable-filter-menu（列头筛选面板）、.vtable__menu-element（右键菜单）、
  .vtable__bubble-tooltip-element（气泡提示）、.vtable__dropdown/.vtable__popup、
  vtable-editor-dropdown（单元格编辑器下拉：无 class 面板 + .virtual-option 选项）

返回每层的：类型 kind、可见性、scope（iframe/top）、容器 CSS（可直接交给
analyze_current_page 聚焦或 page_interact 定位）、标题/文本摘要、视口坐标
（iframe 已叠加偏移）、z-index。可选 include_hidden 列出隐藏态常驻容器
（默认只返回当前可见弹层，降 token）。

基于真实页面 DOM 探查：antd 隐藏容器保留 class 标记（如
ant-select-dropdown-hidden / ant-modal-mask-hidden / ant-dropdown-hidden）；
VTable 隐藏节点用 --hidden 后缀 class 且 z-index 为 -9999（menu-element）
或 --hidden class（bubble-tooltip）——可见性判定需同时检查
display/visibility/opacity/rect/class/z-index，单纯 getBoundingClientRect
会误报（hidden 态仍返回尺寸）。
"""
from __future__ import annotations

from fastmcp import Context
from fastmcp.tools import tool
from mcp.types import Icon

from qa_automation.components.tools.browser import (
    _active_iframe_frame,
    _err,
    _lifecycle,
)

_OVERLAYS_ICON = Icon(
    src="data:image/svg+xml;base64,PHN2ZyB4bWxucz0naHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmcnIHdpZHRoPScyNCcgaGVpZ2h0PScyNCc+PHBhdGggZD0nTTQgMTBsMTYgMCAwIDgtMTYgMHonIGZpbGw9J25vbmUnIHN0cm9rZT0nJTIzZmZmJyBzdHJva2Utd2lkdGg9JzEuNScvPjxwYXRoIGQ9J00xMiA0bDQgNGgtOHonIGZpbGw9J25vbmUnIHN0cm9rZT0nJTIzZmZmJyBzdHJva2Utd2lkdGg9JzEuNScvPjwvc3ZnPg==",
    mime_type="image/svg+xml",
)

# 统一弹层扫描 JS：返回 [{kind, visible, cls, rect, pos, zIndex, text, title, container}]
_OVERLAYS_JS = r"""
(includeHidden) => {
  const out = [];
  const visible = (el) => {
    if (!el) return false;
    const st = getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden' || st.visibility === 'collapse') return false;
    if (Number.parseFloat(st.opacity || '1') === 0) return false;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    return true;
  };
  // VTable 隐藏特征：--hidden class 或 z-index 为负（menu-element 用 -9999 隐藏）
  const vtableHidden = (el) => {
    const cls = String(el.className || '');
    if (/--hidden\b/.test(cls) || /\bhidden\b/.test(cls)) return true;
    const z = Number.parseInt(getComputedStyle(el).zIndex || '0', 10);
    return Number.isFinite(z) && z < 0;
  };
  const cssOf = (el) => {
    let base;
    if (el.id) {
      base = '#' + CSS.escape(el.id);
    } else {
      const parts = [];
      let node = el;
      while (node && node.nodeType === 1 && node !== document.body) {
        let sel = node.tagName.toLowerCase();
        const cls = Array.from(node.classList || []).slice(0, 2);
        if (cls.length) sel += '.' + cls.map(c => CSS.escape(c)).join('.');
        parts.unshift(sel);
        node = node.parentElement;
      }
      base = parts.slice(-3).join(' > ');
    }
    try {
      const matches = Array.from(document.querySelectorAll(base));
      if (matches.length > 1) {
        const idx = matches.indexOf(el);
        if (idx >= 0) return base + ' >> nth=' + idx;
      }
    } catch (e) { /* 保持 base */ }
    return base;
  };
  const push = (kind, el, title) => {
    if (!el) return;
    const cls = String(el.className || '');
    // antd 显式 hidden class 标记
    const antdHidden = /ant-(modal-mask|select-dropdown|dropdown|popover|tooltip|picker|cascader)-hidden/.test(cls)
      || cls.includes('ant-modal-mask-hidden');
    const vHidden = kind.startsWith('vtable') && vtableHidden(el);
    const vis = visible(el) && !antdHidden && !vHidden;
    if (!includeHidden && !vis) return;
    const r = el.getBoundingClientRect();
    const txt = (el.innerText || '').replace(/\s+/g, ' ').trim();
    out.push({
      kind,
      visible: vis,
      cls: cls.slice(0, 160),
      container: cssOf(el),
      title: (title || '').slice(0, 120),
      text: txt.slice(0, 200),
      rect: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
      pos: getComputedStyle(el).position,
      zIndex: getComputedStyle(el).zIndex,
    });
  };

  // ---- antd portal 弹层 ----
  for (const wrap of document.querySelectorAll('.ant-modal-wrap')) {
    const modal = wrap.querySelector('.ant-modal') || wrap;
    const title = (modal.querySelector('.ant-modal-title, .ant-modal-header')?.innerText || '').trim();
    push('modal', modal, title);
  }
  for (const d of document.querySelectorAll('.ant-drawer')) push('drawer', d);
  for (const d of document.querySelectorAll('.ant-select-dropdown')) push('dropdown', d);
  for (const d of document.querySelectorAll('.ant-dropdown:not(.ant-select-dropdown)')) push('dropdown', d);
  for (const p of document.querySelectorAll('.ant-popover, .ant-popconfirm')) push('popover', p);
  for (const t of document.querySelectorAll('.ant-tooltip')) push('tooltip', t);
  for (const m of document.querySelectorAll('.ant-message-notice')) push('message', m);
  for (const n of document.querySelectorAll('.ant-notification-notice')) push('notification', n);
  for (const c of document.querySelectorAll('.ant-cascader-dropdown')) push('dropdown', c);
  for (const p of document.querySelectorAll('.ant-picker-dropdown, .ant-calendar-picker-dropdown, .ant-calendar-picker-container')) {
    push('picker', p);
  }

  // ---- VTable 自绘弹层（canvas 同级兄弟 / body 下）----
  for (const m of document.querySelectorAll('.vtable-filter-menu')) push('vtable-filter', m);
  for (const m of document.querySelectorAll('.vtable__menu-element')) push('vtable-menu', m);
  for (const m of document.querySelectorAll('.vtable__bubble-tooltip-element')) push('vtable-tooltip', m);
  for (const m of document.querySelectorAll('.vtable__dropdown, .vtable__popup')) push('vtable-dropdown', m);
  // VTable 单元格编辑器下拉：无 class 的 fixed/absolute 面板 + .virtual-option 选项
  // （自定义编辑器，如排产物料/单号选择；面板为 canvas 同级兄弟节点或 body 下 portal，
  //   无任何 vtable__ 标记，只能通过选项特征识别）
  const seenPanels = new Set();
  const optionPanels = [];
  for (const opt of document.querySelectorAll('.virtual-option')) {
    let p = opt.parentElement;
    while (p && p !== document.body) {
      const st = getComputedStyle(p);
      if (st.position === 'fixed'
          || (st.position === 'absolute' && Number.parseInt(st.zIndex || '0', 10) > 0)) {
        if (!seenPanels.has(p)) {
          seenPanels.add(p);
          optionPanels.push(p);
        }
        break;
      }
      p = p.parentElement;
    }
  }
  for (const p of optionPanels) {
    const pcls = String(p.className || '');
    // 显式枚举过的 vtable/antd 容器不重复上报
    if (/vtable__|filter-menu|menu-element|bubble-tooltip|ant-select|ant-dropdown/.test(pcls)) continue;
    const inVTable = p.closest('div.vtable, .vtable-container') !== null;
    push(inVTable ? 'vtable-editor-dropdown' : 'dropdown', p, '单元格编辑器下拉');
  }
  // canvas 同级可见兄弟节点兜底（自定义 vtable 弹层 class 未枚举时）
  for (const host of document.querySelectorAll('div.vtable, .vtable-container')) {
    const canvas = host.querySelector('canvas');
    if (!canvas) continue;
    for (const ch of host.children) {
      if (ch === canvas || ch.tagName !== 'DIV') continue;
      const cls = String(ch.className || '');
      // 编辑器下拉面板已按 .virtual-option 特征上报，跳过避免重复
      if (ch.querySelector('.virtual-option')) continue;
      if (!/vtable__|filter-menu|dropdown|popup|panel|menu/i.test(cls)) continue;
      // 显式枚举过的 class 已在上方报告，兜底跳过避免重复
      if (cls.includes('vtable__bubble-tooltip-element')) continue;
      if (cls.includes('vtable__menu-element')) continue;
      if (cls.includes('vtable-filter-menu')) continue;
      if (cls.includes('input-container')) continue;
      push('vtable-menu', ch);
    }
  }
  return out;
}
"""


@tool(
    title="Page: Detect Overlays",
    description="快速检测当前页面（顶层 DOM + 激活 iframe）中的弹层/浮层/弹窗/消息提示气泡："
    "antd modal/drawer/下拉/popover/tooltip/message/notification/日期下拉 + VTable 自绘弹层"
    "（列头筛选面板/右键菜单/气泡）。返回每层类型、可见性、scope、容器 CSS（可交 "
    "analyze_current_page 聚焦或 page_interact 定位）、标题/文本摘要、视口坐标、z-index。"
    "include_hidden=true 时同时列出隐藏态常驻容器（了解页面可打开的弹层全集）；"
    "默认 false 只返回当前可见弹层（降 token）。",
    icons=[_OVERLAYS_ICON],
    tags={"overlay", "layer", "popup", "modal", "message", "qa"},
)
async def detect_overlays(
    ctx: Context,
    session: str | None = None,
    include_hidden: bool = False,
    max_results: int = 40,
) -> dict:
    """检测页面弹层/浮层/弹窗/消息提示气泡。

    Args:
        session: 目标会话名（多账号场景必须显式指定；缺省使用激活会话）。
        include_hidden: True 时列出隐藏态常驻弹层容器；默认 False 只返回可见弹层。
        max_results: 返回的弹层数量上限（每 scope）。
    """
    try:
        page = await _lifecycle(ctx).page(session)
    except Exception as exc:
        return _err(exc)

    layers: list[dict] = []
    try:
        # 激活 iframe 优先（业务页 portal 渲染在 iframe 内），顶层兜底
        frame = await _active_iframe_frame(page)
        if frame is not None:
            raw = await frame.evaluate(_OVERLAYS_JS, include_hidden)
            iframe_loc = page.locator(
                '.ant-tabs-tabpane[role="tabpanel"][aria-hidden="false"] iframe'
            )
            offset = (0.0, 0.0)
            if await iframe_loc.count() > 0:
                box = await iframe_loc.first.bounding_box()
                offset = (box["x"] if box else 0, box["y"] if box else 0)
            for layer in raw[:max_results]:
                r = layer["rect"]
                layers.append(
                    {
                        "kind": layer["kind"],
                        "visible": layer["visible"],
                        "scope": "iframe",
                        "cls": layer["cls"],
                        "container": layer["container"],
                        "title": layer["title"],
                        "text": layer["text"],
                        "x": r["x"] + offset[0],
                        "y": r["y"] + offset[1],
                        "w": r["w"],
                        "h": r["h"],
                        "pos": layer["pos"],
                        "z_index": layer["zIndex"],
                    }
                )
        raw_top = await page.evaluate(_OVERLAYS_JS, include_hidden)
        for layer in raw_top[:max_results]:
            r = layer["rect"]
            layers.append(
                {
                    "kind": layer["kind"],
                    "visible": layer["visible"],
                    "scope": "top",
                    "cls": layer["cls"],
                    "container": layer["container"],
                    "title": layer["title"],
                    "text": layer["text"],
                    "x": r["x"],
                    "y": r["y"],
                    "w": r["w"],
                    "h": r["h"],
                    "pos": layer["pos"],
                    "z_index": layer["zIndex"],
                }
            )
    except Exception as exc:
        return {"ok": False, "error": f"overlay scan failed: {type(exc).__name__}: {exc}"}

    visible_layers = [l for l in layers if l["visible"]]
    hidden_layers = [l for l in layers if not l["visible"]]

    result: dict = {
        "ok": True,
        "count": len(layers),
        "visible_count": len(visible_layers),
        "hidden_count": len(hidden_layers),
        "visible_layers": visible_layers,
        "hidden_layers": hidden_layers if include_hidden else [],
        "include_hidden": include_hidden,
    }
    # 焦点弹层：第一个可见的 modal/dropdown/vtable 弹层（供 analyze 聚焦/交互）
    focus = next(
        (
            l
            for l in visible_layers
            if l["kind"] in ("modal", "drawer", "dropdown", "vtable-filter", "vtable-menu", "vtable-dropdown", "vtable-editor-dropdown", "picker")
        ),
        None,
    )
    if focus:
        result["focus_layer"] = focus
    return result

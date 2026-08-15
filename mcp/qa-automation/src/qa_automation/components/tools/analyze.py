"""页面分析工具：识别当前激活功能页并提取可交互元素定位信息。

结构识别（顶层 DOM）：
- 面包屑 .ant-breadcrumb → 页面路径（如"产品工艺 > 清洗改机设置"）
- 标签导航 .ant-tabs-nav → 已打开功能页标签 + 激活标签
- 激活 tabpanel（role="tabpanel" 且 aria-hidden="false"）内的 iframe → 当前激活功能页

元素提取（顶层 DOM + 激活 iframe + 聚焦弹层）：
- 按钮/输入框/下拉框/链接等可交互控件，输出：唯一 CSS、XPath、get_by_role 参数、
  视口绝对坐标（元素中心，iframe 已叠加偏移）、ref、input_type
- 弹层聚焦裁剪：页面存在可见弹层（modal/dropdown）时，只输出弹层内元素
  （当前关注点），裁剪底层页面元素以降低 token；消息（ant-message）单独报告文本
"""
from __future__ import annotations

from fastmcp import Context
from fastmcp.tools import tool
from mcp.types import Icon

from qa_automation.browser.lifecycle import PlaywrightLifecycle

_ANALYZE_ICON = Icon(
    src="data:image/svg+xml;base64,PHN2ZyB4bWxucz0naHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmcnIHdpZHRoPScyNCcgaGVpZ2h0PScyNCc+PHBhdGggZD0nTTMgMTJsNi02IDYgNi02IDZ6JyBmaWxsPSdub25lIHN0cm9rZT0nJTIzMWY3YWU0JyBzdHJva2Utd2lkdGg9JzInLz48cGF0aCBkPSdNMTUgNWw2IDYtNiA2JyBmaWxsPSdub25lIHN0cm9rZT0nJTIzMWY3YWU0JyBzdHJva2Utd2lkdGg9JzInLz48L3N2Zz4=",
    mime_type="image/svg+xml",
)

# 可交互元素扫描（scope 参数：CSS 选择器限定容器，null 扫全文档；iframe 内传 null）
# 支持 "sel >> nth=N" 后缀消歧（_FOCUS_LAYER_JS 生成；querySelector 不支持 nth）
_SCAN_JS = r"""
(scopeSelector) => {
  let scope = document;
  if (scopeSelector) {
    const m = String(scopeSelector).match(/^(.*?)\s*>>\s*nth=(\d+)$/);
    if (m) {
      const els = document.querySelectorAll(m[1]);
      scope = els[+m[2]] || null;
    } else {
      scope = document.querySelector(scopeSelector);
    }
  }
  if (!scope) return [];
  const results = [];
  const seen = new Set();
  const visible = (el) => {
    let cur = el;
    while (cur && cur.nodeType === 1) {
      const cls = String(cur.className || '');
      const st = getComputedStyle(cur);
      if (cur.hidden || cur.getAttribute('aria-hidden') === 'true'
          || /hidden/i.test(cls) || st.display === 'none'
          || st.visibility === 'hidden' || st.visibility === 'collapse'
          || Number.parseFloat(st.opacity || '1') === 0) return false;
      cur = cur.parentElement;
    }
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const getRole = (el) => {
    const explicit = el.getAttribute('role');
    if (explicit) return explicit;
    const t = el.tagName.toLowerCase();
    if (t === 'button') return 'button';
    if (t === 'a' && el.href) return 'link';
    if (t === 'select') return 'combobox';
    if (t === 'textarea') return 'textbox';
    if (t === 'input') {
      const type = (el.type || 'text').toLowerCase();
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      if (['button', 'submit', 'reset', 'image'].includes(type)) return 'button';
      if (type === 'number') return 'spinbutton';
      if (type === 'range') return 'slider';
      if (type === 'search') return 'searchbox';
      return 'textbox';
    }
    if (el.closest('.ant-switch')) return 'switch';
    if (el.closest('.ant-checkbox-wrapper')) return 'checkbox';
    if (el.closest('.ant-radio-wrapper')) return 'radio';
    if (el.closest('.ant-select-item-option, .ant-select-dropdown-menu-item, .ant-cascader-menu-item')) return 'option'; 'option';
    return null;
  };
  const antComponentSel = '.ant-select, .ant-picker, .ant-cascader, .ant-tree-select, .ant-checkbox-wrapper, .ant-radio-wrapper, .ant-switch';
  const getName = (el) => {
    const labelledby = el.getAttribute('aria-labelledby');
    if (labelledby) {
      const text = labelledby.split(/\s+/).map(id => {
        const n = document.getElementById(id);
        return n ? (n.innerText || '') : '';
      }).join(' ').trim();
      if (text) return text;
    }
    const ariaLabel = el.getAttribute('aria-label');
    if (ariaLabel) return ariaLabel.trim();
    if (el.id) {
      const lab = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (lab && lab.innerText) return lab.innerText.trim();
    }
    const parentLabel = el.closest('label');
    if (parentLabel && parentLabel.innerText) return parentLabel.innerText.trim();
    const antComp = el.closest(antComponentSel);
    const t = el.tagName.toLowerCase();
    const roleAttr = el.getAttribute('role') || '';
    const isTextLike = t === 'button' || t === 'a'
      || ['button', 'link', 'menuitem', 'tab', 'option', 'checkbox', 'radio', 'switch'].includes(roleAttr);
    if (isTextLike) {
      const own = (el.innerText || '').replace(/\s+/g, ' ').trim();
      if (own) return own.slice(0, 80);
    }
    if (t === 'input' && ['button', 'submit', 'reset'].includes((el.type || '').toLowerCase())) {
      const v = (el.value || '').trim();
      if (v) return v.slice(0, 80);
    }
    const antInput = antComp ? antComp.querySelector('input[aria-label], input[placeholder], input') : null;
    return (el.getAttribute('placeholder') || el.title
      || (antInput ? (antInput.getAttribute('aria-label') || antInput.getAttribute('placeholder') || '') : '')
      || (antComp ? (antComp.innerText || '').replace(/\s+/g, ' ').trim() : '')
    ).trim().slice(0, 80);
  };
  const uniqueCss = (el) => {
    const pick = (sel) => {
      try {
        const matches = Array.from((scope === document ? document : scope).querySelectorAll(sel));
        if (matches.length <= 1) return sel;
        const idx = matches.indexOf(el);
        return idx >= 0 ? sel + ' >> nth=' + idx : sel;
      } catch (e) { return sel; }
    };
    if (el.id) return pick('#' + CSS.escape(el.id));
    if (el.name) return pick('[name="' + el.name + '"]');
    if (el.getAttribute('data-testid')) return pick('[data-testid="' + el.getAttribute('data-testid') + '"]');
    const antRoot = el.matches(antComponentSel) ? el : null;
    if (antRoot) {
      const stable = Array.from(antRoot.classList).find(c => /^ant-(select|picker|cascader|tree-select|checkbox-wrapper|radio-wrapper|switch)$/.test(c));
      if (stable) return pick('.' + stable);
    }
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && node !== document.body) {
      if (node.id) { parts.unshift('#' + CSS.escape(node.id)); break; }
      let sel = node.tagName.toLowerCase();
      const cls = Array.from(node.classList || [])
        .filter(c => !c.includes('active') && !c.includes('focus') && !c.includes('hover'));
      if (cls.length) sel += '.' + cls.slice(0, 2).join('.');
      parts.unshift(sel);
      node = node.parentElement;
    }
    return pick(parts.slice(-3).join(' > '));
  };
  const xpath = (el) => {
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && node !== document) {
      let idx = 1;
      let sib = node.previousElementSibling;
      while (sib) { if (sib.tagName === node.tagName) idx++; sib = sib.previousElementSibling; }
      const tag = node.tagName.toLowerCase();
      parts.unshift(idx > 1 ? tag + '[' + idx + ']' : tag);
      node = node.parentElement;
    }
    return '/' + parts.join('/');
  };
  const selector = 'button, input, select, textarea, a[href], [role="button"], [role="combobox"],'
    + ' [role="checkbox"], [role="radio"], [role="switch"], [role="textbox"], [role="searchbox"],'
    + ' [role="spinbutton"], [role="slider"], [role="tab"], [role="menuitem"], [role="option"],'
    + ' .ant-switch, .ant-select, .ant-picker, .ant-cascader, .ant-tree-select,'
    + ' .ant-select-item-option, .ant-select-dropdown-menu-item, .ant-cascader-menu-item,'
    + ' .ant-checkbox-wrapper, .ant-radio-wrapper';
  scope.querySelectorAll(selector).forEach((el) => {
    if (!visible(el)) return;
    const role = getRole(el);
    if (!role) return;
    const rect = el.getBoundingClientRect();
    const key = el.tagName + '|' + rect.x + '|' + rect.y;
    if (seen.has(key)) return;
    seen.add(key);
    results.push({
      type: el.tagName.toLowerCase(),
      role,
      name: getName(el),
      css: uniqueCss(el),
      xpath: xpath(el),
      input_type: el.tagName.toLowerCase() === 'input' ? (el.getAttribute('type') || 'text') : '',
      cx: rect.x + rect.width / 2,
      cy: rect.y + rect.height / 2,
    });
  });
  return results;
}
"""

# 弹层聚焦检测：可见 modal / 下拉 / 消息（antd portal 结构）
_FOCUS_LAYER_JS = r"""
() => {
  const visible = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
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
    // 消歧：基础选择器不唯一时附加索引（_SCAN_JS 支持 ">> nth=N"）
    try {
      const matches = Array.from(document.querySelectorAll(base));
      if (matches.length > 1) {
        const idx = matches.indexOf(el);
        if (idx >= 0) return base + ' >> nth=' + idx;
      }
    } catch (e) { /* 保持 base */ }
    return base;
  };
  // modal（非 mask-hidden 且可见）
  for (const wrap of document.querySelectorAll('.ant-modal-wrap')) {
    if (wrap.classList.contains('ant-modal-mask-hidden')) continue;
    const modal = wrap.querySelector('.ant-modal');
    if (modal && visible(modal)) {
      const title = (modal.querySelector('.ant-modal-title')?.innerText || '').trim();
      return { kind: 'modal', title, container: cssOf(modal) };
    }
  }
  // 下拉（ant-select-dropdown 非 hidden 且可见）
  for (const drop of document.querySelectorAll('.ant-select-dropdown')) {
    if (drop.classList.contains('ant-select-dropdown-hidden')) continue;
    if (visible(drop)) {
      return { kind: 'dropdown', container: cssOf(drop) };
    }
  }
  // 消息（ant-message-notice 存在）
  const msgs = [...document.querySelectorAll('.ant-message-notice')].filter(visible);
  if (msgs.length) {
    return {
      kind: 'message',
      text: msgs.map(m => (m.innerText || '').trim()).filter(Boolean).join(' | '),
    };
  }
  return null;
}
"""


def _lifecycle(ctx: Context) -> PlaywrightLifecycle:
    return ctx.lifespan_context["lifecycle"]


async def _active_iframe_frame(page):
    """返回激活 tabpanel 内的 iframe Frame；无则 None。"""
    try:
        iframe_loc = page.locator(
            '.ant-tabs-tabpane[role="tabpanel"][aria-hidden="false"] iframe'
        )
        if await iframe_loc.count() == 0:
            return None
        frame_el = iframe_loc.first
        name = await frame_el.get_attribute("name") or ""
        src = await frame_el.get_attribute("src") or ""
        for f in page.frames:
            if (name and f.name == name) or (not name and src and f.url == src):
                return f
        return await frame_el.content_frame()
    except Exception:  # noqa: BLE001
        return None


@tool(
    title="Page: Analyze Current",
    description="识别当前激活功能页（面包屑/标签栏/激活 iframe）并提取可交互元素：顶层 DOM 与激活 iframe"
    "内按钮/输入框/下拉框/链接等的定位信息（唯一 CSS、XPath、get_by_role 参数、视口绝对坐标）。"
    "存在可见弹层（modal/下拉）时自动聚焦弹层、裁剪底层页面元素以降低 token；"
    "消息（ant-message）单独报告文本。",
    icons=[_ANALYZE_ICON],
    tags={"browser", "page", "qa"},
)
async def analyze_current_page(
    ctx: Context,
    session: str | None = None,
    max_elements: int = 80,
) -> dict:
    """分析当前激活功能页（顶层 + 激活 iframe + 聚焦弹层）。

    Args:
        session: 目标会话名（多账号场景必须显式指定；缺省使用激活会话）。
        max_elements: 返回的可交互元素数量上限。
    """
    lc = _lifecycle(ctx)
    try:
        page = await lc.page(session)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    # 1) 面包屑
    breadcrumb: list[str] = []
    try:
        breadcrumb = await page.locator(".ant-breadcrumb .ant-breadcrumb-link").all_inner_texts()
    except Exception:  # noqa: BLE001
        pass

    # 2) 标签导航
    tabs: list[dict] = []
    try:
        tab_locs = page.locator(".ant-tabs-nav .ant-tabs-tab")
        count = await tab_locs.count()
        for i in range(count):
            t = tab_locs.nth(i)
            cls = await t.get_attribute("class") or ""
            text = (await t.inner_text()).strip().replace("\n", " ")
            tabs.append({"index": i, "title": text, "active": "ant-tabs-tab-active" in cls})
    except Exception:  # noqa: BLE001
        pass

    # 3) 弹层聚焦检测（激活 iframe 优先——业务页 portal 渲染在 iframe 内；
    #    顶层工作台菜单下拉（如"到达菜单"）可能同时可见，业务焦点优先）
    focus_layer = None
    focus_frame = None
    frame = await _active_iframe_frame(page)
    if frame is not None:
        try:
            layer = await frame.evaluate(_FOCUS_LAYER_JS)
            if layer:
                focus_layer = {**layer, "scope": "iframe"}
                focus_frame = frame
        except Exception:  # noqa: BLE001
            pass
    if focus_layer is None:
        try:
            layer = await page.evaluate(_FOCUS_LAYER_JS)
            if layer:
                focus_layer = {**layer, "scope": "top"}
        except Exception:  # noqa: BLE001
            pass

    iframe_info: dict | None = None
    elements: list[dict] = []
    try:
        if focus_layer and focus_layer.get("kind") in ("modal", "dropdown"):
            # 聚焦弹层：只扫描弹层内元素（裁剪底层页面，降低 token）
            container = focus_layer.get("container")
            if container:
                offset = (0.0, 0.0)
                target = page
                if focus_frame is not None:
                    target = focus_frame
                    iframe_loc = page.locator(
                        '.ant-tabs-tabpane[role="tabpanel"][aria-hidden="false"] iframe'
                    )
                    if await iframe_loc.count() > 0:
                        box = await iframe_loc.first.bounding_box()
                        offset = (box["x"] if box else 0, box["y"] if box else 0)
                raw = await target.evaluate(_SCAN_JS, container)
                for i, el in enumerate(raw[:max_elements]):
                    elements.append(
                        {
                            "ref": f"e{i + 1}",
                            "type": el["type"],
                            "role": el["role"],
                            "name": el["name"],
                            "input_type": el.get("input_type", ""),
                            "css": el["css"],
                            "xpath": el["xpath"],
                            "gbr": {"role": el["role"], "name": el["name"]},
                            "x": round(el["cx"] + offset[0], 1),
                            "y": round(el["cy"] + offset[1], 1),
                            "frame": "layer",
                        }
                    )
        elif focus_layer and focus_layer.get("kind") == "message":
            pass  # 消息仅报告文本（无交互元素收集）
        else:
            # 无弹层：顶层 DOM 扫描
            raw_top = await page.evaluate(_SCAN_JS, None)
            for i, el in enumerate(raw_top[:max_elements]):
                elements.append(
                    {
                        "ref": f"e{i + 1}",
                        "type": el["type"],
                        "role": el["role"],
                        "name": el["name"],
                        "input_type": el.get("input_type", ""),
                        "css": el["css"],
                        "xpath": el["xpath"],
                        "gbr": {"role": el["role"], "name": el["name"]},
                        "x": round(el["cx"], 1),
                        "y": round(el["cy"], 1),
                        "frame": "top",
                    }
                )
            # 激活 iframe 扫描
            iframe_loc = page.locator(
                '.ant-tabs-tabpane[role="tabpanel"][aria-hidden="false"] iframe'
            )
            if await iframe_loc.count() > 0:
                frame_el = iframe_loc.first
                box = await frame_el.bounding_box()
                src = await frame_el.get_attribute("src") or ""
                fid = await frame_el.get_attribute("id") or ""
                fname = await frame_el.get_attribute("name") or ""
                iframe_info = {"src": src, "id": fid, "name": fname}
                frame = None
                for f in page.frames:
                    if (fname and f.name == fname) or (not fname and src and f.url == src):
                        frame = f
                        break
                if frame is None:
                    frame = await frame_el.content_frame()
                if frame is not None:
                    raw_frame = await frame.evaluate(_SCAN_JS, None)
                    fx = box["x"] if box else 0
                    fy = box["y"] if box else 0
                    for i, el in enumerate(raw_frame[:max_elements]):
                        elements.append(
                            {
                                "ref": f"e{len(elements) + i + 1}",
                                "type": el["type"],
                                "role": el["role"],
                                "name": el["name"],
                                "input_type": el.get("input_type", ""),
                                "css": el["css"],
                                "xpath": el["xpath"],
                                "gbr": {"role": el["role"], "name": el["name"]},
                                "x": round(el["cx"] + fx, 1),
                                "y": round(el["cy"] + fy, 1),
                                "frame": "iframe",
                            }
                        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"scan failed: {type(exc).__name__}: {exc}"}

    result: dict = {
        "ok": True,
        "breadcrumb": breadcrumb,
        "tabs": tabs,
        "active_iframe": iframe_info,
        "elements": elements,
        "stats": {"elements": len(elements), "limit": max_elements},
    }
    if focus_layer:
        result["focus_layer"] = focus_layer
    return result

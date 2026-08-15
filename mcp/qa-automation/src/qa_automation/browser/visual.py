"""虚拟光标 + 目标高亮可视化（借鉴 qa-automation-plugin，适配本框架）。

- 操作前：虚拟光标平滑移动到目标（Windows 指针样式），目标元素显示高亮框（呼吸动画）；
- 点击时：波纹反馈；
- 交互完成后：调用 clear() 将全部特效图层从 DOM 中移除（光标/高亮框/波纹均消失）；
- 剔除参考实现中的标签浮层（.label 黑框浮层）。
"""
from __future__ import annotations

import json

# Windows 11 Dark HD pointer（参考实现同款，32px 透明框 + 热点 5,10）
_CURSOR_IMAGE = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAACEUlEQVR4nO2WzasSURiHH79u3+W9N6JPMoqg"
    "oF3LyDFcC+GyjeIfonVX7dxE0MbtDWoR/gGCq9pJgtgsA1voYkBFmTlNE2c4E4OMedWZ2viDl/la/J73vOc978BOO+30"
    "nxVb45sTBUA8yLjX6x222+3LQEpFEkisAA5Htm3XHaXpdPoGuAicB05FDiGEeC6N6/W6G1KGYbwHrgCXgNORQti2/VK"
    "aqno7tVrNhRiNRh+A60A6Ugh7AcAPMRwOP0YOYQcA/FMIIcSrIIAVEMnQIMRfAJZA7ANnQoMQKwD8ELPZrFepVB6ECi"
    "FOACCjVCr9gSiXyw+Bg1AgxAkB/BDj8fgLcGNhY0YPgK8czWbzBXBVnZipdVYhvjEtYBiGe7UsK+GbF/F1AJLrGGqaR"
    "jabda+ZTMYN0zR/FIvFz6FMS7GkBJqmOa1Wy5tRzmQy+TYYDD51Op3XhULhMXBbzQtZgr2NN6IIAKhWq66pZVmTbrf"
    "7Lp/PPwFk+91TxteAQ+DC1hPTVkdxOp12zRuNhmuu6/pxLpd7CsiWuwvcVBnvK+OzKvPtjub5fP5MGsrl9pa83++/BR"
    "4B94FbvtF8TmWc2mTzLVPcNM0jIcRXIcR3XdePVMZ31FIfKGMv2626SCoW8JxQBnu+nrblFpEdB/xUz6H8I8YC3sUV"
    "hBdSv5Spre5D+0GNLXnv1dP7vtiaOxGWfgOOKpPMpe76TQAAAABJRU5ErkJggg=="
)

_KEY = "__qaMcpVisuals"
HOST_ID = "__qa_mcp_visuals__"

_INSTALL_JS = r"""
(() => {
  const KEY = '__qaMcpVisuals';
  const HOST_ID = '__qa_mcp_visuals__';
  const CURSOR_IMAGE = %CURSOR_IMAGE%;
  const EASING = 'cubic-bezier(0.16, 1, 0.3, 1)';
  if (globalThis[KEY]?.version === 2) { globalThis[KEY].mount(); return; }
  if (globalThis[KEY]) { try { globalThis[KEY].clear(); } catch (_) {} }

  const state = {
    host: null, root: null, cursor: null, highlight: null,
    x: 0, y: 0, anim: null,
  };

  function setCursor(x, y) {
    state.x = x; state.y = y;
    if (state.cursor) state.cursor.style.transform = `translate3d(${x - 5}px, ${y - 10}px, 0)`;
  }
  function mount() {
    if (state.host?.isConnected) return true;
    document.getElementById(HOST_ID)?.remove();
    const host = document.createElement('div');
    host.id = HOST_ID;
    host.style.cssText = 'position:fixed;inset:0;width:100vw;height:100vh;pointer-events:none;z-index:2147483647;overflow:visible;contain:layout style paint';
    const root = host.attachShadow({ mode: 'open' });
    const style = document.createElement('style');
    style.textContent = `
      .cursor { position:fixed;left:0;top:0;width:32px;height:32px;display:block;object-fit:contain;opacity:0;pointer-events:none;image-rendering:auto;will-change:transform,opacity;transition:opacity 100ms ${EASING}; }
      .cursor.on { opacity: 1; }
      .highlight { position:fixed;left:0;top:0;min-width:2px;min-height:2px;--accent:#22d3ee;color:var(--accent);border:2px solid currentColor;border-radius:6px;opacity:0;box-shadow:0 0 0 2px rgb(34 211 238 / 20%), 0 0 18px rgb(34 211 238 / 55%);pointer-events:none;overflow:visible;will-change:transform,opacity;transition:opacity 100ms ${EASING}; }
      .highlight::before { content:'';position:absolute;inset:-5px;border:1px solid currentColor;border-radius:9px;opacity:.48;will-change:transform,opacity;animation:target-breathe 900ms ease-in-out infinite alternate; }
      .ripple { position:fixed;width:16px;height:16px;margin:-8px 0 0 -8px;border:2px solid #22d3ee;border-radius:50%;pointer-events:none;box-shadow:0 0 12px #22d3ee;animation:click-ripple 520ms ease-out forwards; }
      @keyframes target-breathe { from { transform:scale3d(.985,.985,1); opacity:.3; } to { transform:scale3d(1.025,1.025,1); opacity:.68; } }
      @keyframes click-ripple { from { transform:scale(.35); opacity:1; } to { transform:scale(4.2); opacity:0; } }
      @media (prefers-reduced-motion: reduce) {
        .cursor, .highlight { transition-duration:.01ms !important; }
        .highlight::before, .ripple { animation-duration:.01ms !important; animation-iteration-count:1 !important; }
      }
    `;
    const highlight = document.createElement('div');
    highlight.className = 'highlight';
    const cursor = document.createElement('img');
    cursor.className = 'cursor';
    cursor.src = CURSOR_IMAGE;
    cursor.alt = '';
    cursor.draggable = false;
    root.append(style, highlight, cursor);
    (document.documentElement || document.body).appendChild(host);
    state.host = host; state.root = root; state.cursor = cursor; state.highlight = highlight;
    setCursor(state.x, state.y);
    return true;
  }
  function moveTo(x, y) {
    // 返回动画完成 Promise：Python 侧 evaluate 等待，保证真实交互在光标到位后触发
    mount();
    const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
    const distance = Math.hypot(x - state.x, y - state.y);
    // 与参考实现对齐并加深减速（MOVEMENT_SLOWDOWN=3），保证移动轨迹清晰可见
    const duration = reduced ? 0 : Math.round(Math.min(640, Math.max(280, (110 + Math.sqrt(distance) * 7) * 3)));
    state.cursor?.classList.add('on');
    state.anim?.cancel();
    const from = { x: state.x, y: state.y };
    const anim = state.cursor?.animate(
      [
        { transform: `translate3d(${from.x - 5}px, ${from.y - 10}px, 0)` },
        { transform: `translate3d(${x - 5}px, ${y - 10}px, 0)` },
      ],
      { duration, easing: EASING, fill: 'forwards' }
    ) || null;
    state.x = x; state.y = y;
    if (!anim) return Promise.resolve();
    state.anim = anim;
    // cancel/被新动画覆盖 → finished reject（AbortError）→ resolve，不阻塞等待方
    return anim.finished.catch(() => {});
  }
  function target(x, y, w, h) {
    // 显示目标高亮框（呼吸动画）+ 光标移动到元素中心（动画完成后 resolve）
    mount();
    state.cursor?.classList.add('on');
    const hl = state.highlight;
    if (hl) {
      hl.style.left = x + 'px';
      hl.style.top = y + 'px';
      hl.style.width = w + 'px';
      hl.style.height = h + 'px';
      hl.style.opacity = 1;
    }
    return moveTo(x + w / 2, y + h / 2);
  }
  function createRipple(x, y) {
    mount();
    const rip = document.createElement('div');
    rip.className = 'ripple';
    rip.style.left = x + 'px';
    rip.style.top = y + 'px';
    state.root.appendChild(rip);
    setTimeout(() => rip.remove(), 600);
  }
  function clickAt(x, y) {
    // 点击反馈：光标已到位（距离 <1px）直接波纹（无抖动）；否则先移动完成再波纹
    const dist = Math.hypot(x - state.x, y - state.y);
    if (dist < 1) {
      createRipple(x, y);
      return Promise.resolve();
    }
    return moveTo(x, y).then(() => createRipple(x, y));
  }
  function clear() {
    // 交互完成：全部特效图层从 DOM 移除
    state.anim?.cancel();
    state.anim = null;
    state.host?.remove();
    state.host = null; state.root = null; state.cursor = null; state.highlight = null;
    state.x = 0; state.y = 0;
  }
  globalThis[KEY] = { mount, moveTo, target, clickAt, clear, version: 2 };
  mount();
})();
""".replace("%CURSOR_IMAGE%", json.dumps(_CURSOR_IMAGE))


def _invoke(expression: str) -> str:
    return f"(() => {expression})()"


def _key() -> str:
    return json.dumps(_KEY)


class VirtualCursor:
    """页面上注入的虚拟光标 + 目标高亮（交互完成 clear() 从 DOM 移除全部特效）。"""

    @staticmethod
    async def attach(page) -> None:
        """注入特效层（幂等）。"""
        await page.evaluate(_INSTALL_JS)

    @staticmethod
    async def target(page, x: float, y: float, w: float, h: float) -> None:
        """高亮目标元素（呼吸框）+ 光标移动到元素中心。"""
        await page.evaluate(_invoke(f"globalThis[{_key()}].target({x}, {y}, {w}, {h})"))

    @staticmethod
    async def move_to(page, x: float, y: float) -> None:
        """光标平滑移动到视口坐标 (x, y)。"""
        await page.evaluate(_invoke(f"globalThis[{_key()}].moveTo({x}, {y})"))

    @staticmethod
    async def click_at(page, x: float, y: float) -> None:
        """光标移动到位并显示点击波纹。"""
        await page.evaluate(_invoke(f"globalThis[{_key()}].clickAt({x}, {y})"))

    @staticmethod
    async def clear(page) -> None:
        """交互完成：从 DOM 移除全部特效图层。"""
        await page.evaluate(_invoke(f"globalThis[{_key()}]?.clear()"))

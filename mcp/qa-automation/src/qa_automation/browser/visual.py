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
  const POS_KEY = '__qa_mcp_cursor_pos__';
  const CURSOR_IMAGE = %CURSOR_IMAGE%;
  const EASING = 'cubic-bezier(0.16, 1, 0.3, 1)';
  if (globalThis[KEY]?.version === 3) { globalThis[KEY].mount(); return; }
  if (globalThis[KEY]) { try { globalThis[KEY].clear(); } catch (_) {} }

  const savedPos = globalThis[POS_KEY] || { x: 0, y: 0 };
  const state = {
    host: null, root: null, cursor: null, highlight: null,
    x: savedPos.x, y: savedPos.y, anim: null, timers: new Set(),
  };

  function setCursor(x, y) {
    state.x = x; state.y = y;
    globalThis[POS_KEY] = { x, y };
    if (state.cursor) {
      state.cursor.style.transform = `translate3d(${x - 5}px, ${y - 10}px, 0)`;
    }
  }

  function mount() {
    if (state.host?.isConnected && state.root) return true;
    document.getElementById(HOST_ID)?.remove();
    const host = document.createElement('div');
    host.id = HOST_ID;
    host.style.cssText = 'position:fixed !important;inset:0 !important;width:auto !important;height:auto !important;pointer-events:none !important;z-index:2147483647 !important;overflow:hidden !important;contain:strict !important;user-select:none !important;-webkit-user-select:none !important;touch-action:none !important;margin:0 !important;padding:0 !important;border:none !important;';
    const root = host.attachShadow({ mode: 'open' });
    const style = document.createElement('style');
    style.textContent = `
      :host, * { box-sizing: border-box !important; margin: 0; padding: 0; pointer-events: none !important; user-select: none !important; -webkit-user-select: none !important; }
      .cursor { position:fixed !important;left:0 !important;top:0 !important;width:32px;height:32px;display:block;object-fit:contain;opacity:0;pointer-events:none !important;image-rendering:-webkit-optimize-contrast;image-rendering:auto;will-change:transform,opacity;backface-visibility:hidden;transform-style:preserve-3d;transition:opacity 60ms ease; }
      .cursor.on { opacity: 1; }
      .highlight { position:fixed !important;left:0 !important;top:0 !important;box-sizing:border-box !important;min-width:2px;min-height:2px;--accent:#22d3ee;color:var(--accent);border:2px solid currentColor;border-radius:6px;opacity:0;box-shadow:0 0 0 2px rgb(34 211 238 / 20%), 0 0 18px rgb(34 211 238 / 55%);pointer-events:none !important;overflow:visible;will-change:transform,opacity;backface-visibility:hidden;transition:opacity 60ms ease; }
      .highlight::before { content:'';position:absolute;inset:-5px;box-sizing:border-box !important;border:1px solid currentColor;border-radius:9px;opacity:.48;pointer-events:none !important;will-change:transform,opacity;animation:target-breathe 900ms ease-in-out infinite alternate; }
      .ripple { position:fixed !important;left:0 !important;top:0 !important;box-sizing:border-box !important;width:16px;height:16px;margin:-8px 0 0 -8px;border:2px solid #22d3ee;border-radius:50%;pointer-events:none !important;box-shadow:0 0 12px #22d3ee;will-change:transform,opacity;animation:click-ripple 400ms ease-out forwards; }
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
    const parent = document.documentElement || document.body;
    parent.appendChild(host);
    state.host = host; state.root = root; state.cursor = cursor; state.highlight = highlight;

    const curPos = globalThis[POS_KEY] || { x: 0, y: 0 };
    state.x = curPos.x;
    state.y = curPos.y;
    setCursor(state.x, state.y);
    return true;
  }

  function moveTo(x, y) {
    mount();
    const from = { x: state.x, y: state.y };
    const distance = Math.hypot(x - from.x, y - from.y);
    const duration = Math.round(Math.min(160, Math.max(40, (20 + Math.sqrt(distance) * 2) * 1.5)));
    state.cursor?.classList.add('on');

    if (state.anim) {
      try { state.anim.cancel(); } catch(_) {}
      state.anim = null;
    }

    if (document.visibilityState !== 'visible' || duration <= 40 || distance < 2) {
      setCursor(x, y);
      return Promise.resolve();
    }

    const anim = state.cursor?.animate(
      [
        { transform: `translate3d(${from.x - 5}px, ${from.y - 10}px, 0)` },
        { transform: `translate3d(${x - 5}px, ${y - 10}px, 0)` },
      ],
      { duration, easing: EASING, fill: 'forwards' }
    ) || null;

    if (!anim) {
      setCursor(x, y);
      return Promise.resolve();
    }
    state.anim = anim;

    const finish = () => {
      setCursor(x, y);
      if (state.anim === anim) {
        try { anim.cancel(); } catch(_) {}
        state.anim = null;
      }
    };

    const animP = anim.finished.then(finish).catch(finish);
    const timerP = new Promise(r => {
      const tid = setTimeout(() => {
        finish();
        state.timers.delete(tid);
        r();
      }, duration + 10);
      state.timers.add(tid);
    });
    return Promise.race([animP, timerP]);
  }

  function target(x, y, w, h) {
    mount();
    state.cursor?.classList.add('on');
    const hl = state.highlight;
    if (hl) {
      hl.style.left = Math.round(x) + 'px';
      hl.style.top = Math.round(y) + 'px';
      hl.style.width = Math.round(w) + 'px';
      hl.style.height = Math.round(h) + 'px';
      hl.style.opacity = '1';
    }
    return moveTo(x + w / 2, y + h / 2);
  }

  function createRipple(x, y) {
    mount();
    const rip = document.createElement('div');
    rip.className = 'ripple';
    rip.style.left = Math.round(x) + 'px';
    rip.style.top = Math.round(y) + 'px';
    state.root?.appendChild(rip);
    const tid = setTimeout(() => {
      rip.remove();
      state.timers.delete(tid);
    }, 450);
    state.timers.add(tid);
  }

  function clickAt(x, y) {
    const dist = Math.hypot(x - state.x, y - state.y);
    if (dist < 2) {
      createRipple(x, y);
      return Promise.resolve();
    }
    return moveTo(x, y).then(() => createRipple(x, y));
  }

  function drag(x1, y1, x2, y2) {
    mount();
    return moveTo(x1, y1)
      .then(() => {
        createRipple(x1, y1);
        return moveTo(x2, y2);
      })
      .then(() => {
        createRipple(x2, y2);
      });
  }

  function clear() {
    // 交互完成：全部特效图层从 DOM 移除，清理全部定时器与动画；保留全局坐标持久态
    if (state.anim) {
      try { state.anim.cancel(); } catch(_) {}
      state.anim = null;
    }
    for (const tid of state.timers) clearTimeout(tid);
    state.timers.clear();
    state.host?.remove();
    state.host = null; state.root = null; state.cursor = null; state.highlight = null;
  }
  globalThis[KEY] = { mount, moveTo, target, clickAt, drag, clear, version: 3 };
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
        """注入特效层（幂等，带超时保护）。"""
        try:
            import asyncio
            await asyncio.wait_for(page.evaluate(_INSTALL_JS), timeout=0.5)
        except Exception:
            pass

    @staticmethod
    async def target(page, x: float, y: float, w: float, h: float) -> None:
        """高亮目标元素（呼吸框）+ 光标移动到元素中心（带超时保护）。"""
        try:
            import asyncio
            await asyncio.wait_for(
                page.evaluate(_invoke(f"globalThis[{_key()}].target({x}, {y}, {w}, {h})")),
                timeout=0.6,
            )
        except Exception:
            pass

    @staticmethod
    async def move_to(page, x: float, y: float) -> None:
        """光标平滑移动到视口坐标 (x, y)（带超时保护）。"""
        try:
            import asyncio
            await asyncio.wait_for(
                page.evaluate(_invoke(f"globalThis[{_key()}].moveTo({x}, {y})")),
                timeout=0.6,
            )
        except Exception:
            pass

    @staticmethod
    async def click_at(page, x: float, y: float) -> None:
        """光标移动到位并显示点击波纹（带超时保护）。"""
        try:
            import asyncio
            await asyncio.wait_for(
                page.evaluate(_invoke(f"globalThis[{_key()}].clickAt({x}, {y})")),
                timeout=0.6,
            )
        except Exception:
            pass
    @staticmethod
    async def drag(page, from_x: float, from_y: float, to_x: float, to_y: float) -> None:
        """光标移动到起始点按下并平滑拖拽至目标点释放（带超时保护）。"""
        try:
            import asyncio
            await asyncio.wait_for(
                page.evaluate(_invoke(f"globalThis[{_key()}].drag({from_x}, {from_y}, {to_x}, {to_y})")),
                timeout=1.2,
            )
        except Exception:
            pass

    @staticmethod
    async def clear(page) -> None:
        """交互完成：从 DOM 移除全部特效图层（带超时保护）。"""
        try:
            import asyncio
            await asyncio.wait_for(
                page.evaluate(_invoke(f"globalThis[{_key()}]?.clear()")),
                timeout=0.3,
            )
        except Exception:
            pass


import asyncio, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        ctx = browser.contexts[0]
        target = None
        for pg in ctx.pages:
            for fr in pg.frames:
                if "supplierManagement" in (fr.url or ""):
                    target = fr
                    break
            if target:
                break
        if not target:
            print("iframe not found")
            await browser.close()
            return
        # Close all modals first
        closes = await target.query_selector_all("button.ant-modal-close")
        for c in closes:
            try:
                if await c.is_visible():
                    await c.click()
                    await asyncio.sleep(0.3)
            except Exception:
                pass
        await asyncio.sleep(0.5)
        # Scan ALL clickable elements in the toolbar area (y between 270 and 320)
        # Find all elements with class containing 'toolbar', 'action', 'column'
        # Dump all spans/i/buttons with title attributes in iframe
        els = await target.query_selector_all("span[title], i[title], a[title], button[title], [class*=toolbar] span, [class*=action-item]")
        print(f"CANDIDATES: {len(els)}")
        for i, el in enumerate(els):
            try:
                title = await el.get_attribute("title") or ""
                cls = await el.get_attribute("class") or ""
                box = await el.bounding_box()
                vis = await el.is_visible()
                if vis and box and box["y"] > 200 and box["y"] < 500:
                    print(f"[{i}] title='{title}' class='{cls[:60]}' box=({box['x']:.0f},{box['y']:.0f},{box['width']:.0f}x{box['height']:.0f})")
            except Exception:
                pass
        # Also dump all icons in toolbar area
        icons = await target.query_selector_all("i.anticon")
        print(f"ALL ICONS: {len(icons)}")
        for i, ic in enumerate(icons):
            try:
                cls = await ic.get_attribute("class") or ""
                box = await ic.bounding_box()
                vis = await ic.is_visible()
                if vis and box and box["y"] > 200 and box["y"] < 400:
                    print(f"  icon[{i}] class='{cls[:70]}' box=({box['x']:.0f},{box['y']:.0f})")
            except Exception:
                pass
        await browser.close()

asyncio.run(main())

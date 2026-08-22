
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
        # Dump all elements in row y=270-320 (toolbar row) with visible boxes
        all_els = await target.query_selector_all("button, a, i, span[class], div[class*='toolbar'], div[class*='action'], div[class*='header-right'] span")
        print(f"SCAN {len(all_els)} elements")
        seen = set()
        for el in all_els:
            try:
                box = await el.bounding_box()
                if not box or not (await el.is_visible()):
                    continue
                if 260 < box["y"] < 330:
                    cls = (await el.get_attribute("class")) or ""
                    txt = (await el.inner_text()).strip()[:20]
                    key = (round(box['x']), round(box['y']), txt, cls[:40])
                    if key in seen: continue
                    seen.add(key)
                    print(f"({box['x']:.0f},{box['y']:.0f},{box['width']:.0f}x{box['height']:.0f}) text='{txt}' class='{cls[:70]}'")
            except Exception:
                pass
        await browser.close()

asyncio.run(main())


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
        # Look at the top area of the table - search for elements with y < 300 that are interactive
        # Dump all buttons anywhere
        btns = await target.query_selector_all("button")
        print(f"ALL BUTTONS: {len(btns)}")
        for i, b in enumerate(btns):
            try:
                box = await b.bounding_box()
                vis = await b.is_visible()
                if not vis or not box: continue
                txt = (await b.inner_text()).strip()[:20]
                cls = (await b.get_attribute("class")) or ""
                title = (await b.get_attribute("title")) or ""
                aria = (await b.get_attribute("aria-label")) or ""
                print(f"[{i}] ({box['x']:.0f},{box['y']:.0f}) text='{txt}' title='{title}' aria='{aria}' class='{cls[:70]}'")
            except Exception:
                pass
        await browser.close()

asyncio.run(main())


import asyncio, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        ctx = browser.contexts[0]
        target = None
        page = None
        for pg in ctx.pages:
            for fr in pg.frames:
                if "supplierManagement" in (fr.url or ""):
                    target = fr
                    page = pg
                    break
            if target:
                break
        if not target:
            print("iframe not found")
            await browser.close()
            return
        # Find export button in iframe
        btns = await target.query_selector_all("button")
        for i, b in enumerate(btns):
            try:
                txt = (await b.inner_text()).strip()
                cls = (await b.get_attribute("class")) or ""
                if "导出" in txt:
                    box = await b.bounding_box()
                    print(f"EXPORT BTN[{i}]: text='{txt}' box={box} class='{cls[:60]}'")
            except Exception:
                pass
        await browser.close()

asyncio.run(main())

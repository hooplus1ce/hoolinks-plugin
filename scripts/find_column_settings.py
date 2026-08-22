
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
        # Look for toolbar buttons / icons in iframe
        btns = await target.query_selector_all("button")
        print(f"TOTAL BUTTONS: {len(btns)}")
        for i, b in enumerate(btns):
            try:
                cls = await b.get_attribute("class") or ""
                txt = (await b.inner_text()).strip()[:30]
                title = await b.get_attribute("title") or ""
                aria = await b.get_attribute("aria-label") or ""
                if txt or title or aria or "icon" in cls or "toolbar" in cls or "column" in cls:
                    print(f"[{i}] text='{txt}' title='{title}' aria='{aria}' class='{cls[:80]}'")
            except Exception:
                pass
        # Also look for icons with class names suggesting column settings
        icons = await target.query_selector_all("i.anticon, span[class*=icon], span[class*=setting]")
        print(f"ICONS: {len(icons)}")
        for i, ic in enumerate(icons):
            try:
                cls = await ic.get_attribute("class") or ""
                txt = (await ic.inner_text()).strip()[:20]
                if "setting" in cls or "column" in cls or "tool" in cls or "gear" in cls or "filter" in cls:
                    print(f"  icon[{i}] class='{cls[:80]}' text='{txt}'")
            except Exception:
                pass
        await browser.close()

asyncio.run(main())

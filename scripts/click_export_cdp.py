
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
        page = target.page
        # Find export button
        btns = await target.query_selector_all("button")
        for i, b in enumerate(btns):
            try:
                txt = (await b.inner_text()).strip()
                cls = (await b.get_attribute("class")) or ""
                if "导出" in txt and "dropdown" in cls:
                    box = await b.bounding_box()
                    print(f"EXPORT BUTTON: text='{txt}' box={box}")
                    # Click it
                    await b.click()
                    print("clicked export")
                    await asyncio.sleep(1.5)
                    break
            except Exception:
                pass
        # Check dropdown menu items
        menus = await target.query_selector_all("ul.ant-dropdown-menu li")
        for m in menus:
            try:
                txt = (await m.inner_text()).strip()
                print(f"MENU: '{txt}'")
            except Exception:
                pass
        await browser.close()

asyncio.run(main())

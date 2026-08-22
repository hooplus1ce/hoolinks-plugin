
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
        # Click the dropdown icon on 创建组织 header (viewport 208,225 in iframe coordinates? These are viewport coords already)
        # vtable viewport coords are absolute viewport, so click on the page at that point
        page = target.page
        await page.mouse.click(208, 225)
        await asyncio.sleep(1.5)
        # Find the vtable menu and click 列设置
        menu = await target.query_selector(".vtable__menu-element")
        if menu:
            txt = await menu.inner_text()
            print(f"MENU TEXT: {txt}")
            # Click the 列设置 item
            items = await menu.query_selector_all("div, li, span")
            for it in items:
                try:
                    t = (await it.inner_text()).strip()
                    if t == "列设置":
                        box = await it.bounding_box()
                        print(f"列设置 item box: {box}")
                        await it.click()
                        await asyncio.sleep(1.5)
                        break
                except Exception:
                    pass
        else:
            print("menu not found")
        # Check modal
        modal = await target.query_selector("div.ant-modal.vtable-column-setting-modal")
        print(f"COLUMN MODAL: {modal is not None}")
        if modal:
            vis = await modal.is_visible()
            print(f"modal visible: {vis}")
        await browser.close()

asyncio.run(main())

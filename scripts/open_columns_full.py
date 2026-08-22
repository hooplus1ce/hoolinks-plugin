
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
        # Step 1: click dropdown icon on header
        await page.mouse.click(208, 225)
        await asyncio.sleep(2)
        # Step 2: wait for menu and click 列设置
        menu = await target.query_selector(".vtable__menu-element--shown, .vtable__menu-element")
        if menu:
            visible = await menu.is_visible()
            print(f"menu visible: {visible}")
            if visible:
                box = await menu.bounding_box()
                print(f"menu box: {box}")
                # click center of menu
                await page.mouse.click(box["x"] + box["width"]/2, box["y"] + box["height"]/2)
                await asyncio.sleep(2)
            else:
                # re-click dropdown
                await page.mouse.click(208, 225)
                await asyncio.sleep(2)
                box = await menu.bounding_box()
                if box:
                    await page.mouse.click(box["x"] + box["width"]/2, box["y"] + box["height"]/2)
                    await asyncio.sleep(2)
        # Check modal
        modal = await target.query_selector("div.ant-modal.vtable-column-setting-modal")
        if modal:
            print(f"COLUMN MODAL OPEN: {await modal.is_visible()}")
            confirm = await modal.query_selector("button.vtable-column-setting-confirm-btn")
            if confirm:
                print(f"confirm disabled: {await confirm.get_attribute('disabled')}")
        else:
            print("COLUMN MODAL NOT FOUND")
        await browser.close()

asyncio.run(main())

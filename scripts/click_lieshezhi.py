
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
        # Menu item at x=160..235, y=243..275 -> center ~(197, 259)
        await page.mouse.click(197, 259)
        await asyncio.sleep(2)
        modal = await target.query_selector("div.ant-modal.vtable-column-setting-modal")
        print(f"COLUMN MODAL: {modal is not None}")
        if modal:
            print(f"visible: {await modal.is_visible()}")
            # Dump confirm button disabled state
            confirm = await modal.query_selector("button.vtable-column-setting-confirm-btn")
            if confirm:
                print(f"confirm disabled: {await confirm.get_attribute('disabled')}")
        await browser.close()

asyncio.run(main())

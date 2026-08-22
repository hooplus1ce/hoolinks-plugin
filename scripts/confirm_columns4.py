
import asyncio, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        ctx = browser.contexts[0]
        page = None
        target = None
        for pg in ctx.pages:
            for fr in pg.frames:
                if "supplierManagement" in (fr.url or ""):
                    page = pg
                    target = fr
                    break
            if target:
                break
        if not target:
            print("iframe not found")
            await browser.close()
            return
        modal = await target.query_selector("div.ant-modal.vtable-column-setting-modal")
        confirm = await modal.query_selector("button.vtable-column-setting-confirm-btn")
        box = await confirm.bounding_box()
        print(f"confirm box in iframe: {box}")
        # Get iframe position on page
        iframe_el = await page.query_selector("iframe[id^=react_iframe]")
        if iframe_el:
            ibox = await iframe_el.bounding_box()
            print(f"iframe box: {ibox}")
            # The modal appears to be in "top" scope per earlier analyze - try clicking on page directly
            # Try page-level click at the button's reported viewport coords (1244, 185)
            await page.mouse.click(1244, 185)
            print("clicked page at (1244,185)")
            await asyncio.sleep(3)
        vis = await modal.is_visible()
        print(f"modal visible after: {vis}")
        msgs = await page.query_selector_all(".ant-message-notice")
        for m in msgs:
            print(f"MESSAGE: {(await m.inner_text()).strip()[:100]}")
        await browser.close()

asyncio.run(main())

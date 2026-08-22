
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
        modal = await target.query_selector("div.ant-modal.vtable-column-setting-modal")
        if not modal:
            print("modal not found")
            await browser.close()
            return
        confirm = await modal.query_selector("button.vtable-column-setting-confirm-btn")
        await confirm.click()
        print("confirm clicked")
        await asyncio.sleep(2)
        vis = await modal.is_visible()
        print(f"modal visible after confirm: {vis}")
        # Check for message
        msgs = await target.query_selector_all(".ant-message-notice")
        for m in msgs:
            print(f"MESSAGE: {(await m.inner_text()).strip()[:80]}")
        await browser.close()

asyncio.run(main())

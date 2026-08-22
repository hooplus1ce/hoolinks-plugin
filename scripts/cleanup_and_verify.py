
import asyncio, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        ctx = browser.contexts[0]
        # Close any lingering modals on any page
        for pg in ctx.pages:
            closes = await pg.query_selector_all("button.ant-modal-close")
            for c in closes:
                try:
                    if await c.is_visible():
                        await c.click()
                        await asyncio.sleep(0.5)
                except Exception:
                    pass
            try:
                await pg.keyboard.press("Escape")
            except Exception:
                pass
        await asyncio.sleep(1)
        # Verify supplier iframe exists and list its columns via vtable instance
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
        if target:
            # Check columns through the VTable instance in the iframe
            cols = await target.evaluate("""() => {
                const v = window._vtable;
                if (!v) return 'no vtable';
                const all = (v.getAllColumns && v.getAllColumns()) || [];
                return all.map(c => c.title).filter(t => t !== undefined && t !== '_vtable_checkbox');
            }""")
            print(f"IFRAME COLUMNS: {cols}")
        await browser.close()

asyncio.run(main())

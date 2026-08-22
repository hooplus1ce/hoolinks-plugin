
import asyncio, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        ctx = browser.contexts[0]
        page = ctx.pages[0]
        # Close top-level modal
        close_btns = await page.query_selector_all("button.ant-modal-close")
        print(f"CLOSE BUTTONS: {len(close_btns)}")
        for i, c in enumerate(close_btns):
            try:
                if await c.is_visible():
                    await c.click()
                    print(f"clicked close #{i}")
                    await asyncio.sleep(1)
            except Exception:
                pass
        await asyncio.sleep(1)
        # Now check VTable columns via window._vtable
        cols = await page.evaluate("""() => {
            const v = window._vtable;
            if (!v) return 'no vtable';
            const all = v.getAllColumns ? v.getAllColumns() : [];
            return all.map(c => c.title).filter(t => t !== undefined);
        }""")
        print(f"VTABLE COLUMNS: {cols}")
        await browser.close()

asyncio.run(main())

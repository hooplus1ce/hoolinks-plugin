
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
        # Find checkboxes in the column setting modal
        labels = await target.query_selector_all("div.ant-modal.vtable-column-setting-modal label.ant-checkbox-wrapper")
        print(f"COLUMN CHECKBOXES: {len(labels)}")
        for i, lb in enumerate(labels):
            try:
                text = (await lb.inner_text()).strip().replace("\n", "")
                checked = await lb.evaluate("el => { const c = el.querySelector('input.ant-checkbox-input'); return c ? c.checked : null; }")
                box = await lb.bounding_box()
                print(f"  [{i}] '{text}' checked={checked} box={box}")
            except Exception as e:
                print(f"  [{i}] ERROR: {e}")
        await browser.close()

asyncio.run(main())

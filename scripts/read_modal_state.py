
import asyncio, sys, json
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        contexts = browser.contexts
        ctx = contexts[0]
        pages = ctx.pages
        print(f"PAGES: {len(pages)}")
        for i, pg in enumerate(pages):
            print(f"  page {i}: {pg.url}")
        # Find the page with supplierManagement iframe
        target = None
        for pg in pages:
            for fr in pg.frames:
                if "supplierManagement" in (fr.url or ""):
                    target = fr
                    break
            if target:
                break
        if not target:
            # try active page
            target = pages[0].frames[0]
            print("using first frame:", target.url)
        # The modal is inside iframe; find checkboxes
        labels = await target.query_selector_all("label.ant-checkbox-wrapper")
        print(f"CHECKBOX LABELS: {len(labels)}")
        for i, lb in enumerate(labels):
            try:
                text = (await lb.inner_text()).strip().replace("\n", "")
                checked = await lb.evaluate("el => { const c = el.querySelector('input.ant-checkbox-input'); return c ? c.checked : null; }")
                cls = await lb.get_attribute("class") or ""
                print(f"  [{i}] '{text}' checked={checked} class={cls[:60]}")
            except Exception as e:
                print(f"  [{i}] ERROR: {e}")
        await browser.close()

asyncio.run(main())

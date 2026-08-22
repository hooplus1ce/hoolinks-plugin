
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
        # Dump the inner HTML structure of the modal content (truncated)
        modal = await target.query_selector("div.ant-modal.vtable-column-setting-modal")
        if modal:
            html = await modal.inner_html()
            # Print key structural elements
            print(f"MODAL HTML LEN: {len(html)}")
            # Find all labels / checkboxes / list items
            items = await modal.query_selector_all("label, li, span, div[class*=item], div[class*=col]")
            print(f"ITEMS: {len(items)}")
            for i, it in enumerate(items[:60]):
                try:
                    cls = (await it.get_attribute("class")) or ""
                    txt = (await it.inner_text()).strip()[:25]
                    if txt or "checkbox" in cls or "item" in cls or "col" in cls:
                        print(f"  [{i}] class='{cls[:60]}' text='{txt}'")
                except Exception:
                    pass
        else:
            print("modal not found")
        await browser.close()

asyncio.run(main())

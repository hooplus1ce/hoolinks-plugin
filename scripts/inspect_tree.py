
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
        # Get the tree container and dump node structure
        tree = await modal.query_selector(".ant-tree")
        print(f"TREE: {tree is not None}")
        # Find elements containing 传真 text
        els = await modal.query_selector_all("div, span, li")
        for i, el in enumerate(els):
            try:
                txt = (await el.inner_text()).strip()
                if txt == "传真":
                    cls = (await el.get_attribute("class")) or ""
                    tag = await el.evaluate("el => el.tagName")
                    print(f"  [{i}] <{tag}> class='{cls[:70]}'")
            except Exception:
                pass
        # Also print first-level structure of tree container
        if tree:
            html = await tree.inner_html()
            print("TREE HTML (first 2000):")
            print(html[:2000])
        await browser.close()

asyncio.run(main())

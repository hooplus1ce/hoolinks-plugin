
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
        tree = await modal.query_selector(".ant-tree")
        # Find li containing 传真 title
        lis = await tree.query_selector_all("li")
        print(f"LI NODES: {len(lis)}")
        for i, li in enumerate(lis):
            title_el = await li.query_selector("span.ant-tree-title")
            if title_el:
                txt = (await title_el.inner_text()).strip()
                if txt == "传真":
                    cb = await li.query_selector("span.ant-tree-checkbox")
                    cls = await cb.get_attribute("class")
                    print(f"传真 at li[{i}] checkbox: {cls}")
                    await cb.click()
                    await asyncio.sleep(0.5)
                    cls2 = await cb.get_attribute("class")
                    print(f"after click: {cls2}")
                    break
        await browser.close()

asyncio.run(main())

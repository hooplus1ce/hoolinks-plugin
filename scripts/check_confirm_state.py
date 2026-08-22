
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
        confirm = await modal.query_selector("button.vtable-column-setting-confirm-btn")
        disabled = await confirm.get_attribute("disabled")
        print(f"CONFIRM disabled attr: {disabled}")
        cls = await confirm.get_attribute("class")
        print(f"CONFIRM class: {cls}")
        # Check tree node aria-checked for all nodes
        tree = await modal.query_selector(".ant-tree")
        lis = await tree.query_selector_all("li")
        for i, li in enumerate(lis):
            title_el = await li.query_selector("span.ant-tree-title")
            if title_el:
                txt = (await title_el.inner_text()).strip()
                cb = await li.query_selector("span.ant-tree-checkbox")
                cbcls = await cb.get_attribute("class")
                aria = await li.get_attribute("aria-checked")
                print(f"  [{i}] '{txt}' cb='{cbcls}' aria={aria}")
        await browser.close()

asyncio.run(main())

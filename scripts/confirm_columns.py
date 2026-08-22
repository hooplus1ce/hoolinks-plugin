
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
        lis = await tree.query_selector_all("li")
        for i, li in enumerate(lis):
            title_el = await li.query_selector("span.ant-tree-title")
            if title_el:
                txt = (await title_el.inner_text()).strip()
                if txt == "传真":
                    cb = await li.query_selector("span.ant-tree-checkbox")
                    print(f"传真 li[{i}] checkbox class: {await cb.get_attribute('class')}")
                    break
        # Click confirm button
        confirm = await modal.query_selector("button.vtable-column-setting-confirm-btn")
        if confirm:
            print("confirm found, clicking...")
            await confirm.click()
            await asyncio.sleep(1.5)
        # Check modal visibility after
        vis = await modal.is_visible()
        print(f"modal visible after confirm: {vis}")
        await browser.close()

asyncio.run(main())


import asyncio, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        ctx = browser.contexts[0]
        target = None
        # modal scope is top now - find the top frame page
        for pg in ctx.pages:
            if "supplierManagement" in pg.url or "admin" in pg.url:
                target = pg
                break
        if not target:
            print("page not found")
            await browser.close()
            return
        # The modal is in top page or inside the iframe?
        modal = await target.query_selector("div.ant-modal.vtable-column-setting-modal")
        if not modal:
            # try iframe
            for fr in target.frames:
                if "supplierManagement" in (fr.url or ""):
                    modal = await fr.query_selector("div.ant-modal.vtable-column-setting-modal")
                    if modal:
                        target = fr
                        break
        if not modal:
            print("modal not found anywhere")
            await browser.close()
            return
        print(f"modal found in frame url: {target.url[:80]}")
        tree = await modal.query_selector(".ant-tree")
        lis = await tree.query_selector_all("li")
        for i, li in enumerate(lis):
            title_el = await li.query_selector("span.ant-tree-title")
            if title_el:
                txt = (await title_el.inner_text()).strip()
                if txt == "传真":
                    cb = await li.query_selector("span.ant-tree-checkbox")
                    cls = await cb.get_attribute("class")
                    print(f"传真 li[{i}] before: {cls}")
                    # Click the checkbox span - real mouse event
                    await cb.click()
                    await asyncio.sleep(1)
                    cls2 = await cb.get_attribute("class")
                    print(f"传真 li[{i}] after: {cls2}")
                    break
        # Check confirm button state
        confirm = await modal.query_selector("button.vtable-column-setting-confirm-btn")
        if confirm:
            print(f"confirm disabled: {await confirm.get_attribute('disabled')}")
        await browser.close()

asyncio.run(main())

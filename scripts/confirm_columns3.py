
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
        # Check state
        disabled = await confirm.get_attribute("disabled")
        print(f"confirm disabled: {disabled}")
        # Try click with mouse at its position (real mouse event)
        box = await confirm.bounding_box()
        print(f"confirm box: {box}")
        if box:
            await target.mouse.click(box["x"] + box["width"]/2, box["y"] + box["height"]/2)
            print("mouse clicked")
            await asyncio.sleep(3)
        vis = await modal.is_visible()
        print(f"modal visible after mouse click: {vis}")
        msgs = await target.query_selector_all(".ant-message-notice")
        for m in msgs:
            print(f"MESSAGE: {(await m.inner_text()).strip()[:100]}")
        # check checkbox state again
        tree = await modal.query_selector(".ant-tree")
        lis = await tree.query_selector_all("li")
        for i, li in enumerate(lis):
            title_el = await li.query_selector("span.ant-tree-title")
            if title_el:
                txt = (await title_el.inner_text()).strip()
                if txt == "传真":
                    cb = await li.query_selector("span.ant-tree-checkbox")
                    print(f"传真 state: {await cb.get_attribute('class')}")
                    break
        await browser.close()

asyncio.run(main())

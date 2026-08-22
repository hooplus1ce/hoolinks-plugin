
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
        # Find the tree node for 传真 and uncheck it
        nodes = await modal.query_selector_all(".ant-tree-treenode")
        print(f"TREE NODES: {len(nodes)}")
        for i, node in enumerate(nodes):
            try:
                title = (await node.inner_text()).strip()
                if title == "传真":
                    cb = await node.query_selector(".ant-tree-checkbox")
                    cls = await cb.get_attribute("class")
                    print(f"FOUND 传真 node[{i}] checkbox class: {cls}")
                    await cb.click()
                    print("clicked 传真 checkbox")
                    await asyncio.sleep(0.5)
                    cls2 = await cb.get_attribute("class")
                    print(f"after click: {cls2}")
            except Exception as e:
                print(f"node {i} err: {e}")
        await browser.close()

asyncio.run(main())

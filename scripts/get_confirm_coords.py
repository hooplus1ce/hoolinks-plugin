
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
        # Get the iframe bounding box to compute viewport coords
        # Find confirm button and its bounding box within iframe
        confirm = await modal.query_selector("button.vtable-column-setting-confirm-btn")
        box = await confirm.bounding_box()
        print(f"CONFIRM box in iframe: {box}")
        # Get iframe element position in top page
        # The iframe id is react_iframe_44080025
        top_frame = ctx.pages[0]
        iframe_el = None
        for pg in ctx.pages:
            iframe_el = await pg.query_selector("iframe#react_iframe_44080025")
            if iframe_el:
                break
        if iframe_el:
            ibox = await iframe_el.bounding_box()
            print(f"IFRAME box: {ibox}")
            if ibox and box:
                print(f"VIEWPORT coords: x={ibox['x'] + box['x'] + box['width']/2:.1f}, y={ibox['y'] + box['y'] + box['height']/2:.1f}")
        await browser.close()

asyncio.run(main())

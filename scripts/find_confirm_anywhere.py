
import asyncio, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        ctx = browser.contexts[0]
        pages = ctx.pages
        print(f"PAGES: {len(pages)}")
        for i, pg in enumerate(pages):
            print(f"  page {i}: {pg.url}")
            # list frames
            for fr in pg.frames:
                if "supplierManagement" in (fr.url or ""):
                    print(f"    iframe found: {fr.url}")
                    # find iframe element
                    target = fr
                    page = pg
                    modal = await fr.query_selector("div.ant-modal.vtable-column-setting-modal")
                    if modal:
                        confirm = await modal.query_selector("button.vtable-column-setting-confirm-btn")
                        box = await confirm.bounding_box()
                        print(f"    confirm box (iframe-local): {box}")
                        # Get iframe element box on page
                        iframe_el = await pg.query_selector(f"iframe[name='{fr.name}']") if fr.name else None
                        if not iframe_el:
                            # search all iframes
                            all_iframes = await pg.query_selector_all("iframe")
                            for ifr in all_iframes:
                                src = await ifr.get_attribute("src") or ""
                                if "supplierManagement" in src:
                                    iframe_el = ifr
                                    break
                        if iframe_el:
                            ibox = await iframe_el.bounding_box()
                            print(f"    iframe element box: {ibox}")
                            if ibox and box:
                                vx = ibox["x"] + box["x"] + box["width"]/2
                                vy = ibox["y"] + box["y"] + box["height"]/2
                                print(f"    VIEWPORT coords: ({vx:.0f}, {vy:.0f})")
                                await page.mouse.click(vx, vy)
                                print("    clicked")
                                await asyncio.sleep(3)
                                print(f"    modal visible after: {await modal.is_visible()}")
                        else:
                            print("    iframe element not found on page")
                    else:
                        print("    modal NOT in iframe")
        # Also check top-level modals on each page
        for i, pg in enumerate(pages):
            top_modal = await pg.query_selector("div.ant-modal.vtable-column-setting-modal")
            if top_modal:
                print(f"  page {i} has TOP-LEVEL column modal")
                vis = await top_modal.is_visible()
                print(f"    visible: {vis}")
                confirm = await top_modal.query_selector("button.vtable-column-setting-confirm-btn")
                if confirm:
                    box = await confirm.bounding_box()
                    print(f"    top confirm box: {box}")
                    if box and vis:
                        await pg.mouse.click(box["x"] + box["width"]/2, box["y"] + box["height"]/2)
                        print("    clicked top confirm")
                        await asyncio.sleep(3)
                        print(f"    visible after: {await top_modal.is_visible()}")
        await browser.close()

asyncio.run(main())

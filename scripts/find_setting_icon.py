
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
        # Close all visible modals (click each close button)
        closes = await target.query_selector_all("button.ant-modal-close")
        print(f"MODAL CLOSE BUTTONS: {len(closes)}")
        for i, c in enumerate(closes):
            try:
                visible = await c.is_visible()
                if visible:
                    await c.click()
                    print(f"clicked close #{i}")
                    await asyncio.sleep(0.5)
            except Exception as e:
                print(f"close #{i} err: {e}")
        # Press Escape to be safe
        try:
            await target.keyboard.press("Escape")
        except Exception:
            pass
        await asyncio.sleep(1)
        # Find the setting icon position
        icons = await target.query_selector_all("i.anticon-setting")
        print(f"SETTING ICONS: {len(icons)}")
        for i, ic in enumerate(icons):
            try:
                box = await ic.bounding_box()
                vis = await ic.is_visible()
                print(f"  icon[{i}] visible={vis} box={box}")
            except Exception as e:
                print(f"  icon[{i}] err: {e}")
        await browser.close()

asyncio.run(main())

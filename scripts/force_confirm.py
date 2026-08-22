
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
        # Check computed style
        info = await confirm.evaluate("""el => {
            const s = getComputedStyle(el);
            return {
                pointerEvents: s.pointerEvents,
                visibility: s.visibility,
                display: s.display,
                opacity: s.opacity,
                disabled: el.disabled,
                ariaDisabled: el.getAttribute('aria-disabled'),
                zIndex: s.zIndex,
                rect: el.getBoundingClientRect().toJSON()
            };
        }""")
        print(f"CONFIRM INFO: {info}")
        # Try force click
        await confirm.click(force=True, timeout=5000)
        print("force clicked")
        await asyncio.sleep(2)
        vis = await modal.is_visible()
        print(f"modal visible after force click: {vis}")
        # Check for any message
        msgs = await target.query_selector_all(".ant-message-notice")
        for m in msgs:
            print(f"MESSAGE: {(await m.inner_text()).strip()[:100]}")
        await browser.close()

asyncio.run(main())

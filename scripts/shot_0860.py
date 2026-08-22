
import asyncio, os
from playwright.async_api import async_playwright
DEST = r"D:\Developer\Hoolinks\hoolinks-plugin\evidence_assets\基础配置\20260821_APS_JCPZ_0860_01_待定.png"
async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        ctx = browser.contexts[0]
        page = ctx.pages[0]
        await page.screenshot(path=DEST, full_page=False)
        print("SAVED", os.path.exists(DEST), DEST)
        await browser.close()
asyncio.run(main())

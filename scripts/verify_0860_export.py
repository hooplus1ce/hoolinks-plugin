
import asyncio, os, json, glob
from playwright.async_api import async_playwright

WORK = r"D:\Developer\Hoolinks\hoolinks-plugin"
DLDIR = os.path.join(WORK, "downloads")
os.makedirs(DLDIR, exist_ok=True)

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        ctx = browser.contexts[0]
        page = ctx.pages[0]
        target = None
        for f in page.frames:
            if "supplierManagement" in (f.url or ""):
                target = f
                break
        if not target:
            print("NO_IFRAME")
            return
        print("IFRAME", target.url)
        cdp = await ctx.new_cdp_session(page)
        await cdp.send("Browser.setDownloadBehavior", {"behavior":"allow","downloadPath":DLDIR,"eventsEnabled":True})
        # close any open dropdown by pressing Escape
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.5)
        # open export dropdown inside iframe
        btn = target.locator("div.ant-row > div > button.ant-btn.ant-dropdown-trigger").first
        await btn.click(timeout=8000)
        await asyncio.sleep(0.8)
        item = target.locator("li.ant-dropdown-menu-item", has_text="按自定义列导出").first
        await item.click(timeout=5000)
        print("CLICKED_EXPORT")
        await asyncio.sleep(4)
        files = sorted(glob.glob(os.path.join(DLDIR, "*")), key=os.path.getmtime)
        print("DL_FILES", files)
        import openpyxl
        for f in files:
            if f.lower().endswith(".xlsx"):
                try:
                    wb = openpyxl.load_workbook(f, read_only=True)
                    ws = wb.active
                    headers = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
                    print("XLSX_HEADERS", [str(c) for c in headers])
                    wb.close()
                except Exception as e:
                    print("XLSX_ERR", f, e)
        cols = await target.evaluate("window._vtable ? window._vtable.allColumns.map(c=>c.title) : null")
        print("VTABLE_COLS", cols)
        await browser.close()

asyncio.run(main())

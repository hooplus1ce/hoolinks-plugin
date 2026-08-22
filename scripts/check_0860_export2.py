
import asyncio, os, json, glob, time
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
            print("NO_IFRAME"); return
        cdp = await ctx.new_cdp_session(page)
        await cdp.send("Browser.setDownloadBehavior", {"behavior":"allow","downloadPath":DLDIR,"eventsEnabled":True})
        await page.keyboard.press("Escape"); await asyncio.sleep(0.5)
        btn = target.locator("div.ant-row > div > button.ant-btn.ant-dropdown-trigger").first
        await btn.click(timeout=8000); await asyncio.sleep(0.8)
        item = target.locator("li.ant-dropdown-menu-item", has_text="按自定义列导出").first
        await item.click(timeout=5000); print("CLICKED"); 
        await asyncio.sleep(4)
        files = sorted(glob.glob(os.path.join(DLDIR, "*")), key=os.path.getmtime)
        print("NEWEST", os.path.basename(files[-1]), "mtime", time.strftime("%H:%M:%S", time.localtime(os.path.getmtime(files[-1]))))
        out = {"newest": os.path.basename(files[-1])}
        import openpyxl
        f = files[-1]
        wb = openpyxl.load_workbook(f, read_only=True)
        ws = wb.active
        hdr = [str(c) for c in next(ws.iter_rows(min_row=1,max_row=1)) if c is not None]
        out["headers"] = hdr
        wb.close()
        json.dump(out, open(os.path.join(WORK,"scripts","0860_check2.json"),"w",encoding="utf-8"), ensure_ascii=False, indent=2)
        print("SAVED")
        await browser.close()

asyncio.run(main())

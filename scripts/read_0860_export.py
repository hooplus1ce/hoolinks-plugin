
import glob, os, json, openpyxl
WORK = r"D:\Developer\Hoolinks\hoolinks-plugin"
DLDIR = os.path.join(WORK, "downloads")
files = sorted(glob.glob(os.path.join(DLDIR, "*")), key=os.path.getmtime)
result = {}
for f in files[-3:]:
    if f.lower().endswith(".xlsx"):
        try:
            wb = openpyxl.load_workbook(f, read_only=True)
            ws = wb.active
            rows = list(ws.iter_rows(min_row=1, max_row=3, values_only=True))
            result[f] = {"headers": [str(c) for c in rows[0] if c is not None], "ncols": len(rows[0]), "first_rows": [list(r) for r in rows[1:]]}
            wb.close()
        except Exception as e:
            result[f] = {"error": str(e)}
json.dump(result, open(os.path.join(WORK, "scripts", "0860_export_headers.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("done", len(files))

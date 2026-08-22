
import glob, os, json, openpyxl
WORK = r"D:\Developer\Hoolinks\hoolinks-plugin"
DLDIR = os.path.join(WORK, "downloads")
files = sorted(glob.glob(os.path.join(DLDIR, "*")), key=os.path.getmtime)
f = files[-1]
wb = openpyxl.load_workbook(f, data_only=True)
ws = wb.active
print("SHEET", ws.title, "dims", ws.dimensions, "max_col", ws.max_column)
hdr = []
for c in range(1, ws.max_column+1):
    hdr.append(ws.cell(row=1, column=c).value)
json.dump({"file": os.path.basename(f), "headers": hdr, "max_col": ws.max_column}, open(os.path.join(WORK,"scripts","0860_hdr.json"),"w",encoding="utf-8"), ensure_ascii=False, indent=2)
print("saved")

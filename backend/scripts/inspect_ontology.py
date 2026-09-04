import openpyxl, glob, os

base = os.path.join(os.path.dirname(__file__), "..", "data")
for f in sorted(glob.glob(os.path.join(base, "*.xlsx"))):
    print("="*70)
    print("FILE:", os.path.basename(f))
    wb = openpyxl.load_workbook(f, read_only=True, data_only=True)
    for ws in wb.worksheets:
        print(f"  -- sheet: {ws.title}")
        rows = list(ws.iter_rows(values_only=True))
        for r in rows[:6]:
            # 只打印非空单元格
            cells = [str(c) for c in r if c is not None]
            if cells:
                print("     ", " | ".join(cells)[:160])
    wb.close()

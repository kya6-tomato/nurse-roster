"""現在のシフト表をエクセル(.xlsx)として出力するファイル。印刷・配布用。

看護師が行、日付が列の並びで出力する。1行目に期間を表示し、
A列:番号／B列:役職／C列:氏名／D列以降:日付ごとの列（日にちのみ表示）／最後の列:合計。
その日に夜勤待機の担当であれば「○」を入れる。固定の休み希望（絶対に動かさない休み）が
出ている日のマスは、色を変えて分かるようにする。
"""
from __future__ import annotations

import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from scheduler.models import Nurse, Period
from scheduler import calendar_utils as cal

TITLE_FONT = Font(bold=True, size=13)
HEADER_FILL = PatternFill(start_color="DDEBF7", end_color="DDEBF7", fill_type="solid")
WEEKEND_FILL = PatternFill(start_color="FCE4E4", end_color="FCE4E4", fill_type="solid")
MARK_FILL = PatternFill(start_color="E2F0D9", end_color="E2F0D9", fill_type="solid")
LOCKED_OFF_FILL = PatternFill(start_color="FCE4A6", end_color="FCE4A6", fill_type="solid")
CONFLICT_FILL = PatternFill(start_color="F4A6A6", end_color="F4A6A6", fill_type="solid")
TOTAL_FILL = PatternFill(start_color="EEF2F6", end_color="EEF2F6", fill_type="solid")
THIN = Side(style="thin", color="BBBBBB")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

TITLE_ROW = 1
HEADER_ROW = 2
WEEKDAY_ROW = 3
DATA_START_ROW = 4

NAME_COL = 3
FIRST_DATE_COL = 4


def export_period(period: Period, nurses: dict[str, Nurse]) -> io.BytesIO:
    wb = Workbook()
    ws = wb.active
    ws.title = "シフト表"

    dates = [cal.parse_date(a.date) for a in period.assignments]
    total_col = FIRST_DATE_COL + len(dates)

    title_cell = ws.cell(row=TITLE_ROW, column=1, value=f"期間: {period.start_date} 〜 {period.end_date}")
    title_cell.font = TITLE_FONT

    headers = ["番号", "役職", "氏名"]
    for col, text in enumerate(headers, start=1):
        c = ws.cell(row=HEADER_ROW, column=col, value=text)
        c.font = Font(bold=True)
        c.fill = HEADER_FILL
        c.border = BORDER
        c.alignment = Alignment(horizontal="center", vertical="center")
        ws.cell(row=WEEKDAY_ROW, column=col).border = BORDER

    for i, d in enumerate(dates):
        col = FIRST_DATE_COL + i
        c1 = ws.cell(row=HEADER_ROW, column=col, value=d)
        c1.number_format = "d"
        c1.font = Font(bold=True)
        c1.fill = HEADER_FILL
        c1.border = BORDER
        c1.alignment = Alignment(horizontal="center")
        c2 = ws.cell(row=WEEKDAY_ROW, column=col, value=cal.weekday_jp(d))
        c2.font = Font(bold=True, size=9)
        c2.border = BORDER
        c2.alignment = Alignment(horizontal="center")
        if cal.is_holiday_or_weekend(d):
            c1.fill = WEEKEND_FILL
            c2.fill = WEEKEND_FILL

    total_header = ws.cell(row=HEADER_ROW, column=total_col, value="合計")
    total_header.font = Font(bold=True)
    total_header.fill = HEADER_FILL
    total_header.border = BORDER
    total_header.alignment = Alignment(horizontal="center", vertical="center")
    ws.cell(row=WEEKDAY_ROW, column=total_col).border = BORDER

    active_nurses = [n for n in nurses.values() if n.active]  # 看護師管理の一覧と同じ並び順

    assigned_dates: dict[str, set[str]] = {n.id: set() for n in active_nurses}
    for a in period.assignments:
        if a.nurse_a in assigned_dates:
            assigned_dates[a.nurse_a].add(a.date)
        if a.nurse_b in assigned_dates:
            assigned_dates[a.nurse_b].add(a.date)

    locked_off: set[tuple[str, str]] = {
        (r.nurse_id, r.date) for r in period.shift_requests
        if r.type == "day_off_request" and r.locked
    }

    for row_offset, n in enumerate(active_nurses):
        row = DATA_START_ROW + row_offset
        ws.cell(row=row, column=1, value=row_offset + 1).border = BORDER
        ws.cell(row=row, column=2, value=n.role).border = BORDER
        ws.cell(row=row, column=NAME_COL, value=n.name).border = BORDER

        total = 0
        for i, a in enumerate(period.assignments):
            col = FIRST_DATE_COL + i
            cell = ws.cell(row=row, column=col)
            cell.border = BORDER
            cell.alignment = Alignment(horizontal="center")

            is_working = a.date in assigned_dates[n.id]
            is_locked_off = (n.id, a.date) in locked_off

            if is_working and is_locked_off:
                # 本来あってはいけない状態（固定の休みなのに勤務になっている）を目立たせる
                cell.value = "○"
                cell.font = Font(bold=True, color="B3261E")
                cell.fill = CONFLICT_FILL
                total += cal.load_weight(cal.parse_date(a.date))
            elif is_working:
                cell.value = "○"
                cell.font = Font(bold=True, color="1F4E79")
                cell.fill = MARK_FILL
                total += cal.load_weight(cal.parse_date(a.date))
            elif is_locked_off:
                cell.value = "休"
                cell.font = Font(bold=True, size=9, color="8A5A00")
                cell.fill = LOCKED_OFF_FILL

        total_cell = ws.cell(row=row, column=total_col, value=total)
        total_cell.font = Font(bold=True)
        total_cell.fill = TOTAL_FILL
        total_cell.border = BORDER
        total_cell.alignment = Alignment(horizontal="center")

    ws.freeze_panes = ws.cell(row=DATA_START_ROW, column=FIRST_DATE_COL).coordinate
    ws.column_dimensions["A"].width = 5
    ws.column_dimensions["B"].width = 9
    ws.column_dimensions["C"].width = 13
    for i in range(len(dates)):
        ws.column_dimensions[get_column_letter(FIRST_DATE_COL + i)].width = 3.3
    ws.column_dimensions[get_column_letter(total_col)].width = 7

    note = ws.cell(
        row=DATA_START_ROW + len(active_nurses) + 1, column=1,
        value="※「休」は固定の休み希望が出ている日。赤い○は、固定の休み希望なのに勤務になっている（要確認）",
    )
    note.font = Font(size=9, color="8A5A00")

    _add_load_summary(wb, period, nurses)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _add_load_summary(wb: Workbook, period: Period, nurses: dict[str, Nurse]) -> None:
    ws = wb.create_sheet("勤務回数まとめ")
    ws.append(["氏名", "合計個数(平日1・土日祝2)", "個別の上限"])
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = HEADER_FILL

    loads: dict[str, int] = {nid: 0 for nid, n in nurses.items() if n.active}
    for a in period.assignments:
        d = cal.parse_date(a.date)
        w = cal.load_weight(d)
        if a.nurse_a in loads:
            loads[a.nurse_a] += w
        if a.nurse_b in loads:
            loads[a.nurse_b] += w

    for nid, total in loads.items():  # 看護師管理の一覧と同じ並び順
        n = nurses[nid]
        ws.append([n.name, total, n.max_load if n.max_load is not None else ""])

    ws.column_dimensions["A"].width = 16
    ws.column_dimensions["B"].width = 20
    ws.column_dimensions["C"].width = 12

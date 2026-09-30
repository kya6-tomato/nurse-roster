"""エクセルの勤務希望シート（休み希望一覧）を読み込むファイル。

対応しているシートの形式:
  A列: 番号（連番。読み込みでは使わない）
  B列: 役職（読み込みでは使わない）
  C列: 氏名
  D列以降: 日付ごとの列。1行目に日付、2行目に曜日、3行目以降が看護師ごとの行。
           その日に出勤できない場合は「×」を入力する。

氏名は、サイトにすでに登録されている看護師の氏名と完全一致している必要がある。
一致しない氏名が1件でもあれば、ImportError_ を投げて取り込み全体を中断する
（表記ゆれをそのまま反映してしまう事故を防ぐため、一部だけの取り込みはしない）。
"""
from __future__ import annotations

import datetime

from openpyxl import load_workbook

from scheduler.models import Nurse, Period, ShiftRequest

DATE_ROW = 1
DATA_START_ROW = 3
NAME_COL = 3  # C列
FIRST_DATE_COL = 4  # D列

UNAVAILABLE_MARKS = {"×", "x", "X", "ｘ", "Ｘ", "✕", "✗"}


class ImportError_(Exception):
    """取り込み時のエラー。メッセージはそのまま画面に表示できる日本語にしてある。"""


def _cell_to_date(value, year_hint: int) -> datetime.date | None:
    """セルの値をdatetime.dateに変換する。年が書かれていない場合はyear_hintを補う。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str):
        s = value.strip()
        for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
            try:
                return datetime.datetime.strptime(s, fmt).date()
            except ValueError:
                pass
        for fmt in ("%m/%d", "%m-%d", "%m月%d日"):
            try:
                d = datetime.datetime.strptime(s, fmt)
                return datetime.date(year_hint, d.month, d.day)
            except ValueError:
                pass
    return None


def parse_shift_requests(file, period: Period, nurses: dict[str, Nurse]) -> list[ShiftRequest]:
    """勤務希望シート(.xlsx)を読み込み、ShiftRequestのリストを返す。

    file: ファイルパス、またはopenpyxlに渡せるファイルのようなオブジェクト。
    期間に含まれない日付の列は無視する。
    """
    try:
        wb = load_workbook(file, data_only=True)
    except Exception as e:
        raise ImportError_(f"エクセルファイルを読み込めませんでした（{e}）。.xlsx形式のファイルか確認してください。") from e
    ws = wb.active

    period_dates = {a.date for a in period.assignments}
    year_hint = int(period.start_date[:4])

    col_to_date: dict[int, str] = {}
    for col in range(FIRST_DATE_COL, ws.max_column + 1):
        raw = ws.cell(row=DATE_ROW, column=col).value
        d = _cell_to_date(raw, year_hint)
        if d is None:
            continue
        iso = d.isoformat()
        if iso in period_dates:
            col_to_date[col] = iso

    if not col_to_date:
        raise ImportError_(
            f"1行目から、この期間（{period.start_date}〜{period.end_date}）に含まれる日付の列が"
            "見つかりませんでした。シートの日付や、選んでいる期間を確認してください。"
        )

    name_to_nurse = {n.name: n for n in nurses.values()}

    requests: list[ShiftRequest] = []
    unmatched: list[str] = []

    for row in range(DATA_START_ROW, ws.max_row + 1):
        raw_name = ws.cell(row=row, column=NAME_COL).value
        if raw_name is None or str(raw_name).strip() == "":
            continue
        name = str(raw_name).strip()

        nurse = name_to_nurse.get(name)
        if nurse is None:
            unmatched.append(f"{row}行目「{name}」")
            continue

        for col, iso_date in col_to_date.items():
            mark = ws.cell(row=row, column=col).value
            if mark is not None and str(mark).strip() in UNAVAILABLE_MARKS:
                requests.append(ShiftRequest(
                    nurse_id=nurse.id, date=iso_date, type="day_off_request", source="excel_import",
                ))

    if unmatched:
        raise ImportError_(
            "サイトに登録されている看護師の氏名と一致しない行がありました。"
            "「看護師管理」ページで氏名の登録・表記を確認してから、もう一度取り込んでください： "
            + " / ".join(unmatched)
        )

    return requests

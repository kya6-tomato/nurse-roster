"""期間（木曜始まり28日間）の日付計算と、祝日判定を行うファイル。

祝日の判定には jpholiday という無料ライブラリを使う。
インストールされていない場合は、土日だけを「休日」として扱うようにフォールバックする
（起動時にエラーで止まらないようにするための保険）。
"""
from __future__ import annotations

import datetime

try:
    import jpholiday
    _HAS_JPHOLIDAY = True
except ImportError:  # pragma: no cover - セットアップ未実施時の保険
    _HAS_JPHOLIDAY = False

PERIOD_LENGTH_DAYS = 28
THURSDAY = 3  # datetime.date.weekday() で 月=0 ... 木=3 ... 日=6

WEEKDAY_JP = ["月", "火", "水", "木", "金", "土", "日"]


def parse_date(s: str) -> datetime.date:
    return datetime.date.fromisoformat(s)


def format_date(d: datetime.date) -> str:
    return d.isoformat()


def is_thursday(d: datetime.date) -> bool:
    return d.weekday() == THURSDAY


def next_valid_start_date(after: datetime.date) -> datetime.date:
    """指定日より後で、最初に来る木曜日を返す。"""
    days_ahead = (THURSDAY - after.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return after + datetime.timedelta(days=days_ahead)


def build_period_dates(start_date: datetime.date) -> list[datetime.date]:
    """木曜始まりの28日間の日付リストを返す。"""
    if not is_thursday(start_date):
        raise ValueError("期間の開始日は木曜日である必要があります")
    return [start_date + datetime.timedelta(days=i) for i in range(PERIOD_LENGTH_DAYS)]


def end_date_for(start_date: datetime.date) -> datetime.date:
    return start_date + datetime.timedelta(days=PERIOD_LENGTH_DAYS - 1)


def is_holiday_or_weekend(d: datetime.date) -> bool:
    """土曜・日曜・（分かれば）祝日を True として扱う。"""
    if d.weekday() >= 5:  # 5=土, 6=日
        return True
    if _HAS_JPHOLIDAY:
        return jpholiday.is_holiday(d)
    return False


def load_weight(d: datetime.date) -> int:
    """平日夜勤=1、土日祝日夜勤=2 という重み付け。"""
    return 2 if is_holiday_or_weekend(d) else 1


def week_index(d: datetime.date, start_date: datetime.date) -> int:
    """期間内での週番号(0-3)。木曜始まりの週をひとまとまりとする。"""
    return (d - start_date).days // 7


def weekday_jp(d: datetime.date) -> str:
    return WEEKDAY_JP[d.weekday()]

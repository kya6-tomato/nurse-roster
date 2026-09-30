"""看護師データ・シフトデータを読み書きするファイル。

データは、アプリのフォルダの外（ドキュメントフォルダの中）に保存する。
こうしておくことで、アプリ本体（このフォルダの中身）を新しいバージョンに
入れ替えても、登録した看護師やシフトのデータは消えずに引き継がれる。

nurses.json … 看護師の一覧（氏名・役職・有効/無効）
periods/<開始日>.json … 期間ごとのシフトデータ
pairs.json … 勤務させてよい2人組の一覧（許可リスト）
"""
from __future__ import annotations

import json
import datetime
import shutil
from pathlib import Path

from scheduler.models import Nurse, Period, DayAssignment
from scheduler import calendar_utils as cal

DATA_DIR = Path.home() / "Documents" / "夜勤待機シフト管理アプリ" / "data"
NURSES_FILE = DATA_DIR / "nurses.json"
PERIODS_DIR = DATA_DIR / "periods"
PAIRS_FILE = DATA_DIR / "pairs.json"
_OLD_TEAM_PAIRS_FILE = DATA_DIR / "team_pairs.json"  # 旧・Bチーム限定ペア設定（あれば移行する）

# 以前のバージョンでは、アプリのフォルダの中の data/ に保存していた。
# そこにデータが残っていて、新しい保存先がまだ無い場合は、そのまま引き継ぐ。
_LEGACY_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def ensure_data_dirs() -> None:
    if not DATA_DIR.exists() and _LEGACY_DATA_DIR.exists():
        # アプリのフォルダの中にあった古い保存先を、新しい保存先へそのままコピーする
        # （古いフォルダ自体は念のため残しておき、削除はしない）
        DATA_DIR.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(_LEGACY_DATA_DIR, DATA_DIR)

    PERIODS_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "uploads").mkdir(parents=True, exist_ok=True)
    if not NURSES_FILE.exists():
        NURSES_FILE.write_text("[]", encoding="utf-8")
    if not PAIRS_FILE.exists():
        if _OLD_TEAM_PAIRS_FILE.exists():
            # チーム制の廃止前に登録されていた組み合わせを、そのまま引き継ぐ
            PAIRS_FILE.write_text(_OLD_TEAM_PAIRS_FILE.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            PAIRS_FILE.write_text("[]", encoding="utf-8")


def load_nurses() -> dict[str, Nurse]:
    ensure_data_dirs()
    raw = json.loads(NURSES_FILE.read_text(encoding="utf-8"))
    nurses = [Nurse.from_dict(d) for d in raw]
    return {n.id: n for n in nurses}


def save_nurses(nurses: dict[str, Nurse]) -> None:
    ensure_data_dirs()
    raw = [n.to_dict() for n in nurses.values()]
    NURSES_FILE.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")


def next_nurse_id(nurses: dict[str, Nurse]) -> str:
    existing_nums = []
    for nid in nurses.keys():
        if nid.startswith("N") and nid[1:].isdigit():
            existing_nums.append(int(nid[1:]))
    next_num = (max(existing_nums) + 1) if existing_nums else 1
    return f"N{next_num:02d}"


def list_periods() -> list[dict]:
    ensure_data_dirs()
    result = []
    for f in sorted(PERIODS_DIR.glob("*.json")):
        raw = json.loads(f.read_text(encoding="utf-8"))
        result.append({
            "period_id": raw["period_id"],
            "start_date": raw["start_date"],
            "end_date": raw["end_date"],
            "status": raw.get("status", "draft"),
        })
    result.sort(key=lambda p: p["start_date"])
    return result


def _period_path(period_id: str) -> Path:
    return PERIODS_DIR / f"{period_id}.json"


def load_period(period_id: str) -> Period:
    path = _period_path(period_id)
    raw = json.loads(path.read_text(encoding="utf-8"))
    return Period.from_dict(raw)


def save_period(period: Period) -> None:
    ensure_data_dirs()
    period.last_edited_at = datetime.datetime.now().isoformat(timespec="seconds")
    path = _period_path(period.period_id)
    path.write_text(json.dumps(period.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def period_exists(period_id: str) -> bool:
    return _period_path(period_id).exists()


def load_other_periods(exclude_period_id: str) -> list[Period]:
    """指定した期間以外の、保存済みの期間をすべて読み込む。

    月をまたぐ勤務回数の均等化で、他の期間の実績を参照するために使う。
    """
    return [
        load_period(meta["period_id"])
        for meta in list_periods()
        if meta["period_id"] != exclude_period_id
    ]


def delete_period(period_id: str) -> None:
    path = _period_path(period_id)
    if path.exists():
        path.unlink()


def create_period(start_date: datetime.date) -> Period:
    if not cal.is_thursday(start_date):
        raise ValueError("期間の開始日は木曜日である必要があります")
    period_id = cal.format_date(start_date)
    if period_exists(period_id):
        raise ValueError(f"期間 {period_id} はすでに存在します")

    dates = cal.build_period_dates(start_date)
    assignments = [DayAssignment(date=cal.format_date(d)) for d in dates]
    period = Period(
        period_id=period_id,
        start_date=period_id,
        end_date=cal.format_date(cal.end_date_for(start_date)),
        status="draft",
        assignments=assignments,
        shift_requests=[],
    )
    save_period(period)
    return period


def save_upload(period_id: str, file_storage) -> Path:
    """アップロードされたエクセルファイルを data/uploads に保存し、保存先のパスを返す。"""
    ensure_data_dirs()
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = Path(file_storage.filename).name  # ディレクトリ区切りなどを取り除く
    dest = DATA_DIR / "uploads" / f"{period_id}_{ts}_{safe_name}"
    file_storage.save(dest)
    return dest


def load_allowed_pairs() -> list[tuple[str, str]]:
    """「この2人組なら勤務させてよい」と登録済みのペア一覧を返す。

    シフトの自動生成は、この一覧にある組み合わせしか使わない（許可リスト方式）。
    """
    ensure_data_dirs()
    raw = json.loads(PAIRS_FILE.read_text(encoding="utf-8"))
    return [tuple(sorted((p[0], p[1]))) for p in raw]


def save_allowed_pairs(pairs: list[tuple[str, str]]) -> None:
    ensure_data_dirs()
    raw = [sorted((a, b)) for a, b in pairs]
    PAIRS_FILE.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")


def suggest_next_start_date() -> datetime.date:
    periods = list_periods()
    if not periods:
        return cal.next_valid_start_date(datetime.date.today() - datetime.timedelta(days=1))
    last_end = cal.parse_date(periods[-1]["end_date"])
    return cal.next_valid_start_date(last_end)

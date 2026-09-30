"""シフト管理アプリで使うデータの形（モデル）をまとめたファイル。

このファイルは「データがどんな形をしているか」だけを定義していて、
保存や読み込みの実際の処理は storage/period_store.py が担当する。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Nurse:
    id: str
    name: str
    role: str = ""  # 役職（任意）
    active: bool = True
    max_load: Optional[int] = None  # 勤務回数(重み付け合計)の個別上限。Noneなら上限なし＝全員一律の均等ルールの対象

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Nurse":
        return Nurse(
            id=d["id"],
            name=d["name"],
            role=d.get("role", ""),
            active=d.get("active", True),
            max_load=d.get("max_load"),
        )


@dataclass
class ShiftRequest:
    nurse_id: str
    date: str  # "2026-08-10" 形式
    type: str = "day_off_request"  # 今後の拡張用。今は「休み希望」のみ
    source: str = "manual"  # "manual" または "excel_import"
    locked: bool = False  # Trueだと「固定」＝自動生成の条件緩和でも絶対に崩さない

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "ShiftRequest":
        return ShiftRequest(
            nurse_id=d["nurse_id"],
            date=d["date"],
            type=d.get("type", "day_off_request"),
            source=d.get("source", "manual"),
            locked=d.get("locked", False),
        )


@dataclass
class DayAssignment:
    date: str
    nurse_a: Optional[str] = None  # NurseのID
    nurse_b: Optional[str] = None
    locked: bool = False  # Trueだと自動生成で上書きされない

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "DayAssignment":
        return DayAssignment(
            date=d["date"],
            nurse_a=d.get("nurse_a"),
            nurse_b=d.get("nurse_b"),
            locked=d.get("locked", False),
        )


@dataclass
class Period:
    period_id: str  # 開始日と同じ "2026-08-06"
    start_date: str
    end_date: str
    status: str = "draft"  # "draft" または "final"
    assignments: list[DayAssignment] = field(default_factory=list)
    shift_requests: list[ShiftRequest] = field(default_factory=list)
    generated_at: Optional[str] = None
    last_edited_at: Optional[str] = None
    solver_meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "period_id": self.period_id,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "status": self.status,
            "assignments": [a.to_dict() for a in self.assignments],
            "shift_requests": [r.to_dict() for r in self.shift_requests],
            "generated_at": self.generated_at,
            "last_edited_at": self.last_edited_at,
            "solver_meta": self.solver_meta,
        }

    @staticmethod
    def from_dict(d: dict) -> "Period":
        return Period(
            period_id=d["period_id"],
            start_date=d["start_date"],
            end_date=d["end_date"],
            status=d.get("status", "draft"),
            assignments=[DayAssignment.from_dict(a) for a in d.get("assignments", [])],
            shift_requests=[ShiftRequest.from_dict(r) for r in d.get("shift_requests", [])],
            generated_at=d.get("generated_at"),
            last_edited_at=d.get("last_edited_at"),
            solver_meta=d.get("solver_meta", {}),
        )


@dataclass
class Violation:
    type: str
    severity: str  # "hard" または "soft"
    nurse_ids: list[str]
    dates: list[str]
    message: str

    def to_dict(self) -> dict:
        return asdict(self)

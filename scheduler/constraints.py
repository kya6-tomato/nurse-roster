"""シフトのルール違反をチェックするファイル。

OR-Tools には依存していない、素のPythonだけのロジック。
・自動生成した直後の確認
・サイト上でマスを手で編集した直後のリアルタイムチェック
の両方から、このファイルの check_schedule() を呼び出して使う。
"""
from __future__ import annotations

from .models import Period, Nurse, Violation
from . import calendar_utils as cal

MIN_LEGAL_INTERVAL = 4  # 中三日(休み3日)を守るための、勤務日と勤務日の最小間隔
MIN_GAP_MAX_COUNT = 2  # 中三日は1期間に最大2回まで
MIN_GAP_MAX_STREAK = 2  # 中三日が3回連続はNG → 連続数の上限は2
PAIR_REPEAT_MAX = 2  # 同じペアは1期間に最大2回まで
LOAD_MAX_DIFF = 1  # 合計個数の差は1以内


def _assigned_day_indices(period: Period, nurse_id: str) -> list[int]:
    start = cal.parse_date(period.start_date)
    indices = []
    for i, a in enumerate(period.assignments):
        if a.nurse_a == nurse_id or a.nurse_b == nurse_id:
            indices.append(i)
    return indices


def _check_rest_gaps(period: Period, nurse: Nurse) -> list[Violation]:
    violations: list[Violation] = []
    days = _assigned_day_indices(period, nurse.id)
    if len(days) < 2:
        return violations

    intervals = [days[i + 1] - days[i] for i in range(len(days) - 1)]
    dates = [period.assignments[i].date for i in days]

    min_gap_streak = 0
    min_gap_total = 0
    for idx, interval in enumerate(intervals):
        d1, d2 = dates[idx], dates[idx + 1]
        if interval < MIN_LEGAL_INTERVAL:
            rest_days = interval - 1
            violations.append(Violation(
                type="MIN_GAP_TOO_SMALL",
                severity="hard",
                nurse_ids=[nurse.id],
                dates=[d1, d2],
                message=f"{nurse.name}さん：{d1}と{d2}の間隔が中{rest_days}日しかありません（最低でも中三日必要です）",
            ))
            min_gap_streak = 0
            continue

        if interval == MIN_LEGAL_INTERVAL:
            min_gap_total += 1
            min_gap_streak += 1
            if min_gap_streak > MIN_GAP_MAX_STREAK:
                violations.append(Violation(
                    type="REPEATED_MIN_GAP",
                    severity="hard",
                    nurse_ids=[nurse.id],
                    dates=[d1, d2],
                    message=f"{nurse.name}さん：中三日ぎりぎりの勤務が3回以上連続しています（{d1}と{d2}の間）",
                ))
        else:
            min_gap_streak = 0

    if min_gap_total > MIN_GAP_MAX_COUNT:
        violations.append(Violation(
            type="TOO_MANY_MIN_GAP",
            severity="hard",
            nurse_ids=[nurse.id],
            dates=dates,
            message=f"{nurse.name}さん：中三日ぎりぎりの勤務がこの期間に{min_gap_total}回あります（最大{MIN_GAP_MAX_COUNT}回まで）",
        ))

    return violations


def month_key(d) -> tuple[int, int]:
    return (d.year, d.month)


def compute_other_periods_month_loads(
    period: Period, other_periods: list[Period]
) -> dict[tuple[tuple[int, int], str], int]:
    """他の（保存済みの）期間から、対象期間がまたがる月それぞれについて、
    各看護師にすでに割り当て済みの重み付き回数を集計する。

    月をまたぐ期間の均等化（自動生成・条件チェックの両方）で使う共通ロジック。
    戻り値は {(年, 月), 看護師ID): 回数} の形。
    """
    dates = [cal.parse_date(a.date) for a in period.assignments]
    months_touched = {month_key(d) for d in dates}

    prior: dict[tuple[tuple[int, int], str], int] = {}
    for other in other_periods:
        if other.period_id == period.period_id:
            continue
        for a in other.assignments:
            d = cal.parse_date(a.date)
            mkey = month_key(d)
            if mkey not in months_touched:
                continue
            w = cal.load_weight(d)
            for nid in (a.nurse_a, a.nurse_b):
                if nid:
                    prior[mkey, nid] = prior.get((mkey, nid), 0) + w
    return prior


def _check_load_balance(period: Period, nurses: dict[str, Nurse]) -> list[Violation]:
    """勤務回数(重み付け合計)をチェックする。

    個別に上限(max_load)を設定した人は、その上限を超えていないかだけを確認し、
    それ以外の人たちの間では、今まで通り「差1個以内」を確認する。
    """
    loads: dict[str, int] = {n.id: 0 for n in nurses.values() if n.active}

    for a in period.assignments:
        d = cal.parse_date(a.date)
        w = cal.load_weight(d)
        if a.nurse_a and a.nurse_a in loads:
            loads[a.nurse_a] += w
        if a.nurse_b and a.nurse_b in loads:
            loads[a.nurse_b] += w

    if not loads:
        return []

    violations: list[Violation] = []

    for nid, total in loads.items():
        cap = nurses[nid].max_load
        if cap is not None and total > cap:
            violations.append(Violation(
                type="MAX_LOAD_EXCEEDED",
                severity="hard",
                nurse_ids=[nid],
                dates=[],
                message=f"{nurses[nid].name}さん：勤務回数が{total}個あり、個別に設定した上限（{cap}個）を超えています",
            ))

    balanced_loads = {nid: v for nid, v in loads.items() if nurses[nid].max_load is None}
    if balanced_loads:
        max_load = max(balanced_loads.values())
        min_load = min(balanced_loads.values())
        if max_load - min_load > LOAD_MAX_DIFF:
            detail = "、".join(
                f"{nurses[nid].name}さん:{v}個" for nid, v in sorted(balanced_loads.items(), key=lambda kv: -kv[1])
            )
            violations.append(Violation(
                type="LOAD_IMBALANCE",
                severity="hard",
                nurse_ids=list(balanced_loads.keys()),
                dates=[],
                message=f"勤務回数の差が{max_load - min_load}個あり、1個以内になっていません（{detail}）",
            ))

    return violations


def _check_month_load_balance(
    period: Period, nurses: dict[str, Nurse], other_periods: list[Period]
) -> list[Violation]:
    """暦月ごとの勤務回数(重み付け合計)もチェックする。

    この期間がまたがる月それぞれについて、他の期間からの実績も合算した上で、
    個別に上限を設定していない人たちの間で「差1個以内」を確認する。
    """
    prior = compute_other_periods_month_loads(period, other_periods)

    this_period_loads: dict[tuple[tuple[int, int], str], int] = {}
    for a in period.assignments:
        d = cal.parse_date(a.date)
        mkey = month_key(d)
        w = cal.load_weight(d)
        for nid in (a.nurse_a, a.nurse_b):
            if nid:
                this_period_loads[mkey, nid] = this_period_loads.get((mkey, nid), 0) + w

    months = sorted({mkey for (mkey, _) in this_period_loads.keys()})
    violations: list[Violation] = []

    for mkey in months:
        totals = {
            n.id: prior.get((mkey, n.id), 0) + this_period_loads.get((mkey, n.id), 0)
            for n in nurses.values() if n.active
        }
        balanced = {nid: v for nid, v in totals.items() if nurses[nid].max_load is None}
        if not balanced:
            continue
        max_load = max(balanced.values())
        min_load = min(balanced.values())
        if max_load - min_load > LOAD_MAX_DIFF:
            year, month = mkey
            detail = "、".join(
                f"{nurses[nid].name}さん:{v}個" for nid, v in sorted(balanced.items(), key=lambda kv: -kv[1])
            )
            violations.append(Violation(
                type="MONTH_LOAD_IMBALANCE",
                severity="hard",
                nurse_ids=list(balanced.keys()),
                dates=[],
                message=f"{year}年{month}月の勤務回数の差が{max_load - min_load}個あり、1個以内になっていません（{detail}）",
            ))

    return violations


def _check_pair_repeats(period: Period, nurses: dict[str, Nurse]) -> list[Violation]:
    counts: dict[tuple[str, str], list[str]] = {}
    for a in period.assignments:
        if a.nurse_a and a.nurse_b:
            key = (a.nurse_a, a.nurse_b)
            counts.setdefault(key, []).append(a.date)

    violations = []
    for (na, nb), dates in counts.items():
        if len(dates) > PAIR_REPEAT_MAX:
            name_a = nurses[na].name if na in nurses else na
            name_b = nurses[nb].name if nb in nurses else nb
            violations.append(Violation(
                type="PAIR_REPEAT_LIMIT",
                severity="hard",
                nurse_ids=[na, nb],
                dates=dates,
                message=f"{name_a}さんと{name_b}さんのペアが{len(dates)}回組まれています（最大{PAIR_REPEAT_MAX}回まで）",
            ))
    return violations


def _check_weekend_off_guarantees(period: Period, nurse: Nurse) -> list[Violation]:
    """看護師一人ひとりについて、
    ・金土日が全て休みの週が最低1回
    ・「金土日が全て休み」または「土日が休み」の週が、合計で最低2回
      （金土日休みが2回でも、金土日休み1回＋土日休み1回でもどちらでもOK）
    あることを確認する。"""
    assigned_ids = set(_assigned_day_indices(period, nurse.id))

    fss_count = 0
    ss_count = 0
    for week in range(4):
        base = week * 7
        fri, sat, sun = base + 1, base + 2, base + 3
        is_fss_off = fri not in assigned_ids and sat not in assigned_ids and sun not in assigned_ids
        is_ss_off = sat not in assigned_ids and sun not in assigned_ids
        if is_fss_off:
            fss_count += 1
        if is_ss_off:
            ss_count += 1

    violations = []
    if fss_count < 1:
        violations.append(Violation(
            type="MISSING_FRI_SAT_SUN_OFF",
            severity="hard",
            nurse_ids=[nurse.id],
            dates=[],
            message=f"{nurse.name}さん：金土日すべて休みの週が1回もありません",
        ))
    if ss_count < 2:
        violations.append(Violation(
            type="MISSING_WEEKEND_OFF_TWICE",
            severity="hard",
            nurse_ids=[nurse.id],
            dates=[],
            message=(
                f"{nurse.name}さん：「金土日休み」または「土日休み」の週が、合計で{ss_count}回しかありません"
                "（2回必要です）"
            ),
        ))
    return violations


def _check_unfilled_and_pairing(
    period: Period, nurses: dict[str, Nurse], allowed_pairs: set[tuple[str, str]]
) -> list[Violation]:
    """担当者が埋まっているか、埋まっている場合は許可リストに登録済みの組み合わせかを確認する。"""
    violations = []
    for a in period.assignments:
        if not a.nurse_a or not a.nurse_b:
            violations.append(Violation(
                type="UNFILLED_DAY",
                severity="hard",
                nurse_ids=[],
                dates=[a.date],
                message=f"{a.date}：担当者が割り当てられていません",
            ))
            continue
        na = nurses.get(a.nurse_a)
        nb = nurses.get(a.nurse_b)
        if not na or not nb:
            continue

        if tuple(sorted((na.id, nb.id))) in allowed_pairs:
            continue
        violations.append(Violation(
            type="PAIRING_INVALID",
            severity="hard",
            nurse_ids=[na.id, nb.id],
            dates=[a.date],
            message=f"{a.date}：{na.name}さんと{nb.name}さんの組み合わせは、登録済みの組み合わせではありません",
        ))
    return violations


def _check_shift_requests(period: Period, nurses: dict[str, Nurse]) -> list[Violation]:
    assigned_by_date: dict[str, set[str]] = {}
    for a in period.assignments:
        s = set()
        if a.nurse_a:
            s.add(a.nurse_a)
        if a.nurse_b:
            s.add(a.nurse_b)
        assigned_by_date[a.date] = s

    violations = []
    for req in period.shift_requests:
        if req.type != "day_off_request":
            continue
        if req.nurse_id in assigned_by_date.get(req.date, set()):
            name = nurses[req.nurse_id].name if req.nurse_id in nurses else req.nurse_id
            label = "固定の休み" if req.locked else "休み希望"
            violations.append(Violation(
                type="REQUEST_CONFLICT",
                severity="hard" if req.locked else "soft",
                nurse_ids=[req.nurse_id],
                dates=[req.date],
                message=f"{name}さん：{req.date}は{label}でしたが、勤務が割り当てられています",
            ))
    return violations


def check_schedule(
    period: Period,
    nurses: dict[str, Nurse],
    allowed_pairs: list[tuple[str, str]] | None = None,
    other_periods: list[Period] | None = None,
) -> list[Violation]:
    """期間全体のルール違反を洗い出す。nursesはid→Nurseの辞書。

    other_periods を渡すと、月をまたぐ勤務回数の均等化もチェックする
    （他の期間の実績と合算して、暦月ごとの差もチェックする）。
    """
    pairs_set = {tuple(sorted(p)) for p in (allowed_pairs or [])}
    violations: list[Violation] = []

    violations += _check_unfilled_and_pairing(period, nurses, pairs_set)

    for nurse in nurses.values():
        if not nurse.active:
            continue
        violations += _check_rest_gaps(period, nurse)
        violations += _check_weekend_off_guarantees(period, nurse)

    violations += _check_load_balance(period, nurses)
    if other_periods is not None:
        violations += _check_month_load_balance(period, nurses, other_periods)
    violations += _check_pair_repeats(period, nurses)
    violations += _check_shift_requests(period, nurses)

    return violations

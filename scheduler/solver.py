"""OR-Tools(CP-SAT)を使って、条件を満たすシフトを自動生成するファイル。

チーム（A・B）というくくりはなく、看護師は1つのプールとして扱う。
1日は「勤務させてよい」と事前に登録した2人組（許可リスト）から必ず1組を選ぶ。
登録されていない組み合わせは、自動生成では絶対に使わない。

条件が厳しすぎて解けない場合は、段階的に条件を緩めながら再挑戦し、
「何を緩めたか」を日本語のメッセージにして返す（solver_meta["relaxed"]）。
"""
from __future__ import annotations

import datetime

from ortools.sat.python import cp_model

from .models import Nurse, Period, DayAssignment
from . import calendar_utils as cal
from .constraints import (
    MIN_LEGAL_INTERVAL,
    MIN_GAP_MAX_COUNT,
    MIN_GAP_MAX_STREAK,
    PAIR_REPEAT_MAX,
    LOAD_MAX_DIFF,
    month_key,
    compute_other_periods_month_loads,
)

N_DAYS = cal.PERIOD_LENGTH_DAYS
SOLVE_TIME_LIMIT_SECONDS = 20.0

# 緩和のはしご（上から順に試す。緩めるほど下に進む）
_ATTEMPTS = [
    {"requests_hard": True, "pair_repeat_max": PAIR_REPEAT_MAX, "min_gap_max_count": MIN_GAP_MAX_COUNT, "load_max_diff": LOAD_MAX_DIFF},
    {"requests_hard": False, "pair_repeat_max": PAIR_REPEAT_MAX, "min_gap_max_count": MIN_GAP_MAX_COUNT, "load_max_diff": LOAD_MAX_DIFF},
    {"requests_hard": False, "pair_repeat_max": PAIR_REPEAT_MAX + 1, "min_gap_max_count": MIN_GAP_MAX_COUNT, "load_max_diff": LOAD_MAX_DIFF},
    {"requests_hard": False, "pair_repeat_max": PAIR_REPEAT_MAX + 1, "min_gap_max_count": MIN_GAP_MAX_COUNT + 1, "load_max_diff": LOAD_MAX_DIFF},
    {"requests_hard": False, "pair_repeat_max": PAIR_REPEAT_MAX + 1, "min_gap_max_count": MIN_GAP_MAX_COUNT + 1, "load_max_diff": LOAD_MAX_DIFF + 1},
]


class InfeasibleError(Exception):
    pass


def _describe_relaxations(params: dict) -> list[str]:
    notes = []
    if not params["requests_hard"]:
        notes.append("勤務希望を「できるだけ」の希望として扱いました（一部の希望が通らない場合があります）")
    if params["pair_repeat_max"] > PAIR_REPEAT_MAX:
        notes.append(f"同じペアの上限を{PAIR_REPEAT_MAX}回→{params['pair_repeat_max']}回に緩めました")
    if params["min_gap_max_count"] > MIN_GAP_MAX_COUNT:
        notes.append(f"中三日ぎりぎりの勤務の上限を{MIN_GAP_MAX_COUNT}回→{params['min_gap_max_count']}回に緩めました")
    if params["load_max_diff"] > LOAD_MAX_DIFF:
        notes.append(f"勤務回数の差の上限（期間内・月ごとの両方）を{LOAD_MAX_DIFF}個→{params['load_max_diff']}個に緩めました")
    return notes


def _build_and_solve(
    period: Period,
    nurses: dict[str, Nurse],
    pairs: list[tuple[str, str]],
    params: dict,
    other_periods: list[Period],
):
    active_ids = {n.id for n in nurses.values() if n.active}
    all_ids = [n.id for n in nurses.values() if n.active]
    if len(all_ids) < 2:
        raise InfeasibleError("有効な看護師が2人以上必要です")

    allowed_pairs = [p for p in pairs if p[0] in active_ids and p[1] in active_ids]
    if not allowed_pairs:
        raise InfeasibleError(
            "勤務させてよい組み合わせが1つも登録されていません。"
            "「看護師管理」ページの「組み合わせ設定」で、勤務させてよい2人組を登録してください。"
        )

    start = cal.parse_date(period.start_date)
    dates = [start + datetime.timedelta(days=i) for i in range(N_DAYS)]
    date_strs = [cal.format_date(d) for d in dates]
    weights = [cal.load_weight(d) for d in dates]

    model = cp_model.CpModel()

    x = {(n, d): model.NewBoolVar(f"x_{n}_{d}") for n in all_ids for d in range(N_DAYS)}

    # 1日1組：登録済みの許可リストから、必ず1組だけ選ぶ
    pair_used = {
        (p, d): model.NewBoolVar(f"pair_{p[0]}_{p[1]}_{d}")
        for p in allowed_pairs for d in range(N_DAYS)
    }
    for d in range(N_DAYS):
        model.Add(sum(pair_used[p, d] for p in allowed_pairs) == 1)
        for n in all_ids:
            involved = [pair_used[p, d] for p in allowed_pairs if n in p]
            model.Add(x[n, d] == sum(involved))

    # ロック済みの日は固定する
    for d, a in enumerate(period.assignments):
        if a.locked and a.nurse_a and a.nurse_b:
            for n in all_ids:
                model.Add(x[n, d] == (1 if n in (a.nurse_a, a.nurse_b) else 0))

    # 中三日(最低3日空ける)を必須に：どの4日間の窓にも勤務は1回まで
    for n in all_ids:
        for d in range(N_DAYS - MIN_LEGAL_INTERVAL + 1):
            model.Add(sum(x[n, d + k] for k in range(MIN_LEGAL_INTERVAL)) <= 1)

    # 「中三日ぎりぎり」を示す変数（d日とd+4日の両方に勤務＝間の3日は必ず空いている）
    min_gap = {}
    max_start = N_DAYS - MIN_LEGAL_INTERVAL - 1
    for n in all_ids:
        for d in range(max_start + 1):
            v = model.NewBoolVar(f"mingap_{n}_{d}")
            model.Add(v <= x[n, d])
            model.Add(v <= x[n, d + MIN_LEGAL_INTERVAL])
            model.Add(v >= x[n, d] + x[n, d + MIN_LEGAL_INTERVAL] - 1)
            min_gap[n, d] = v

    # 中三日ぎりぎりが3回連続しない
    for n in all_ids:
        for d in range(max_start + 1 - 2 * MIN_LEGAL_INTERVAL):
            model.Add(
                min_gap[n, d] + min_gap[n, d + MIN_LEGAL_INTERVAL] + min_gap[n, d + 2 * MIN_LEGAL_INTERVAL]
                <= MIN_GAP_MAX_STREAK
            )

    # 中三日ぎりぎりは1期間に最大N回まで
    for n in all_ids:
        model.Add(sum(min_gap[n, d] for d in range(max_start + 1)) <= params["min_gap_max_count"])

    # 同じペアの繰り返し上限
    for p in allowed_pairs:
        model.Add(sum(pair_used[p, d] for d in range(N_DAYS)) <= params["pair_repeat_max"])

    # 勤務回数(平日=1・土日祝日=2)の均等化。
    # 個別に上限(max_load)を設定した人は、その人数までなら何回でもOKとして、
    # 「差1個以内」の均等比較からは外す（＝それ以外の人たちの間では、今まで通り差1個以内を守る）。
    loads = {}
    max_possible_load = sum(weights)
    for n in all_ids:
        load_var = model.NewIntVar(0, max_possible_load, f"load_{n}")
        model.Add(load_var == sum(x[n, d] * weights[d] for d in range(N_DAYS)))
        loads[n] = load_var

        max_load = nurses[n].max_load
        if max_load is not None:
            model.Add(load_var <= max_load)

    balanced_ids = [n for n in all_ids if nurses[n].max_load is None]
    for i, n1 in enumerate(balanced_ids):
        for n2 in balanced_ids[i + 1:]:
            model.Add(loads[n1] - loads[n2] <= params["load_max_diff"])
            model.Add(loads[n2] - loads[n1] <= params["load_max_diff"])

    # 暦月ごとの均等化。この期間がまたがる月（最大2つ）それぞれについて、
    # 他の期間からの実績（固定値）も合算した上で、期間内の均等化と同じルールで比較する。
    prior_month_loads = compute_other_periods_month_loads(period, other_periods)
    months_touched = sorted({month_key(d) for d in dates})
    for mkey in months_touched:
        month_day_indices = [i for i, d in enumerate(dates) if month_key(d) == mkey]
        month_loads = {}
        for n in all_ids:
            prior = prior_month_loads.get((mkey, n), 0)
            mvar = model.NewIntVar(0, prior + max_possible_load, f"monthload_{n}_{mkey[0]}_{mkey[1]}")
            model.Add(mvar == prior + sum(x[n, d] * weights[d] for d in month_day_indices))
            month_loads[n] = mvar

        for i, n1 in enumerate(balanced_ids):
            for n2 in balanced_ids[i + 1:]:
                model.Add(month_loads[n1] - month_loads[n2] <= params["load_max_diff"])
                model.Add(month_loads[n2] - month_loads[n1] <= params["load_max_diff"])

    # 金土日(または土日)が丸ごと休みの週が、1人ずつ合計2回以上あるようにする。
    # 「金土日休み」の週は「土日休み」の週にも数えられるので、
    # 「金土日休みが2回」でも「金土日休み1回＋土日休み1回」でもどちらでも条件を満たす。
    for n in all_ids:
        off_fss = []
        off_ss = []
        for week in range(N_DAYS // 7):
            base = week * 7
            fri, sat, sun = base + 1, base + 2, base + 3
            v1 = model.NewBoolVar(f"offfss_{n}_{week}")
            model.Add(x[n, fri] == 0).OnlyEnforceIf(v1)
            model.Add(x[n, sat] == 0).OnlyEnforceIf(v1)
            model.Add(x[n, sun] == 0).OnlyEnforceIf(v1)
            off_fss.append(v1)

            v2 = model.NewBoolVar(f"offss_{n}_{week}")
            model.Add(x[n, sat] == 0).OnlyEnforceIf(v2)
            model.Add(x[n, sun] == 0).OnlyEnforceIf(v2)
            off_ss.append(v2)
        model.Add(sum(off_fss) >= 1)
        model.Add(sum(off_ss) >= 2)

    # 勤務希望（休み希望）。「固定」は緩和のどの段階でも必ず守る。それ以外はparamsに従う。
    request_penalties = []
    for req in period.shift_requests:
        if req.type != "day_off_request" or req.nurse_id not in all_ids:
            continue
        try:
            d = date_strs.index(req.date)
        except ValueError:
            continue
        if req.locked or params["requests_hard"]:
            model.Add(x[req.nurse_id, d] == 0)
        else:
            miss = model.NewBoolVar(f"reqmiss_{req.nurse_id}_{d}")
            model.Add(x[req.nurse_id, d] <= miss)
            request_penalties.append(miss)

    objective_terms = []
    objective_terms += [min_gap[n, d] for n in all_ids for d in range(max_start + 1)]
    objective_terms += [100 * p for p in request_penalties]
    if objective_terms:
        model.Minimize(sum(objective_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = SOLVE_TIME_LIMIT_SECONDS
    status = solver.Solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None

    assignments = []
    for d in range(N_DAYS):
        working = [n for n in all_ids if solver.Value(x[n, d]) == 1]
        na = working[0] if len(working) > 0 else None
        nb = working[1] if len(working) > 1 else None
        was_locked = period.assignments[d].locked if d < len(period.assignments) else False
        assignments.append(DayAssignment(date=date_strs[d], nurse_a=na, nurse_b=nb, locked=was_locked))
    return assignments, solver.ObjectiveValue() if objective_terms else 0


def solve_period(
    period: Period,
    nurses: dict[str, Nurse],
    pairs: list[tuple[str, str]] | None = None,
    other_periods: list[Period] | None = None,
) -> tuple[list[DayAssignment], dict]:
    """条件を満たすシフトを生成する。緩和のはしごを上から順に試す。

    other_periods を渡すと、それらの実績も加味して暦月ごとの均等化も行う
    （他の期間はすでに確定しているものとして扱い、今回生成する分で帳尻を合わせる）。
    """
    pairs = pairs or []
    other_periods = other_periods or []
    last_error = None
    for params in _ATTEMPTS:
        try:
            result = _build_and_solve(period, nurses, pairs, params, other_periods)
        except InfeasibleError as e:
            last_error = str(e)
            break
        if result is not None:
            assignments, objective_value = result
            meta = {
                "status": "ok",
                "relaxed": _describe_relaxations(params),
                "objective_value": objective_value,
            }
            return assignments, meta

    meta = {
        "status": "infeasible",
        "relaxed": [],
        "objective_value": None,
        "error": last_error or "この条件ではシフトを組めませんでした。組み合わせの登録数や休み希望を見直してください。",
    }
    return [], meta

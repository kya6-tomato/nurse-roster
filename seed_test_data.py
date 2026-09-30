"""テストデータ投入用の使い捨てスクリプト。

看護師・組み合わせ設定・1期間分のシフト（自動生成済み）をデータストアに
直接書き込む。実行後は削除してよい。

安全のため、データストアに既存データ（看護師・組み合わせ・期間のいずれか）
がある場合は何も書き込まずに中断する。
"""
from __future__ import annotations

import datetime
import itertools

from scheduler.models import Nurse
from scheduler.solver import solve_period
from storage import period_store as store

TEST_NURSES = [
    ("テスト一郎", "スタッフ"),
    ("テスト二郎", "スタッフ"),
    ("テスト三郎", "主任"),
    ("テスト四郎", "スタッフ"),
    ("テスト五郎", "スタッフ"),
    ("テスト花子", "スタッフ"),
    ("テスト梅子", "リーダー"),
    ("テスト桃子", "スタッフ"),
    ("テスト菊子", "スタッフ"),
    ("テスト葵子", "スタッフ"),
]

START_DATE = datetime.date(2026, 9, 3)


def main() -> None:
    existing_nurses = store.load_nurses()
    existing_pairs = store.load_allowed_pairs()
    existing_periods = store.list_periods()
    if existing_nurses or existing_pairs or existing_periods:
        raise SystemExit(
            "データストアが空ではありません（看護師 {}人 / 組み合わせ {}件 / "
            "期間 {}件）。安全のため投入を中止しました。".format(
                len(existing_nurses), len(existing_pairs), len(existing_periods)
            )
        )

    nurses: dict[str, Nurse] = {}
    for i, (name, role) in enumerate(TEST_NURSES, start=1):
        nid = f"N{i:02d}"
        nurses[nid] = Nurse(id=nid, name=name, role=role)
    store.save_nurses(nurses)
    print(f"[1/3] 看護師 {len(nurses)} 人を登録しました: " + ", ".join(n.name for n in nurses.values()))

    all_pairs = list(itertools.combinations(nurses.keys(), 2))
    store.save_allowed_pairs(all_pairs)
    print(f"[2/3] 組み合わせ {len(all_pairs)} 件を登録しました（登録した看護師の総当たり）。")

    period = store.create_period(START_DATE)
    print(f"[3/3] 期間 {period.period_id}（〜{period.end_date}）を作成しました。自動生成を実行します...")

    assignments, meta = solve_period(period, nurses, all_pairs, other_periods=[])
    if meta["status"] != "ok":
        print("自動生成に失敗しました:", meta.get("error"))
        raise SystemExit(1)

    period.assignments = assignments
    period.generated_at = datetime.datetime.now().isoformat(timespec="seconds")
    period.solver_meta = meta
    store.save_period(period)

    if meta["relaxed"]:
        print("自動生成が完了しました（条件を一部緩和）：" + " / ".join(meta["relaxed"]))
    else:
        print("自動生成が完了しました。すべての条件を満たしています。")


if __name__ == "__main__":
    main()

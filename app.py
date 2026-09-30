"""看護師 夜勤待機シフト管理アプリの本体。

起動方法（このファイルを直接読む必要はありません）:
  1. setup.bat を最初に1回だけダブルクリック
  2. run_server.bat をダブルクリックするとブラウザが自動で開きます
"""
from __future__ import annotations

import datetime
import threading
import webbrowser

from flask import (
    Flask, render_template, request, redirect, url_for, flash, send_file, abort
)

from scheduler.models import Nurse, ShiftRequest, DayAssignment
from scheduler import calendar_utils as cal
from scheduler.constraints import check_schedule
from scheduler.solver import solve_period
from excel_io.exporter import export_period
from storage import period_store as store

app = Flask(__name__)
app.secret_key = "nurse-roster-local-tool"  # ローカル利用のみのため固定値で問題ありません


@app.route("/")
def index():
    periods = store.list_periods()
    suggested = cal.format_date(store.suggest_next_start_date())
    return render_template("index.html", periods=periods, suggested=suggested)


@app.route("/periods", methods=["POST"])
def create_period():
    start_str = request.form.get("start_date", "")
    try:
        start_date = cal.parse_date(start_str)
        store.create_period(start_date)
    except ValueError as e:
        flash(f"作成できませんでした: {e}", "error")
        return redirect(url_for("index"))
    return redirect(url_for("view_period", period_id=cal.format_date(start_date)))


@app.route("/periods/<period_id>")
def view_period(period_id):
    if not store.period_exists(period_id):
        abort(404)
    period = store.load_period(period_id)
    nurses = store.load_nurses()
    allowed_pairs = store.load_allowed_pairs()
    other_periods = store.load_other_periods(period_id)
    violations = check_schedule(period, nurses, allowed_pairs, other_periods)
    violation_dates = set()
    for v in violations:
        violation_dates.update(v.dates)

    weeks = [period.assignments[i:i + 7] for i in range(0, len(period.assignments), 7)]
    all_active = [n for n in nurses.values() if n.active]  # 看護師管理の一覧と同じ並び順
    active_ids = {n.id for n in all_active}
    request_state = {}
    for r in period.shift_requests:
        if r.type == "day_off_request":
            request_state[(r.nurse_id, r.date)] = "hard" if r.locked else "soft"

    return render_template(
        "schedule.html",
        period=period,
        weeks=weeks,
        nurses=nurses,
        all_active=all_active,
        active_ids=active_ids,
        violations=violations,
        violation_dates=violation_dates,
        request_state=request_state,
        weekday_jp=cal.weekday_jp,
        parse_date=cal.parse_date,
        is_holiday_or_weekend=cal.is_holiday_or_weekend,
    )


@app.route("/periods/<period_id>/generate", methods=["POST"])
def generate_period(period_id):
    if not store.period_exists(period_id):
        abort(404)
    period = store.load_period(period_id)
    nurses = store.load_nurses()
    allowed_pairs = store.load_allowed_pairs()
    other_periods = store.load_other_periods(period_id)

    try:
        assignments, meta = solve_period(period, nurses, allowed_pairs, other_periods)
    except Exception as e:  # ソルバーの想定外エラーもユーザーに分かる形で表示する
        flash(f"自動生成でエラーが発生しました: {e}", "error")
        return redirect(url_for("view_period", period_id=period_id))

    if meta["status"] != "ok":
        flash(meta.get("error", "この条件ではシフトを組めませんでした。"), "error")
        return redirect(url_for("view_period", period_id=period_id))

    period.assignments = assignments
    period.generated_at = datetime.datetime.now().isoformat(timespec="seconds")
    period.solver_meta = meta
    store.save_period(period)

    if meta["relaxed"]:
        flash("自動生成しました。ただし一部の条件を緩めています：" + " / ".join(meta["relaxed"]), "warn")
    else:
        flash("自動生成しました。すべての条件を満たしています。", "ok")
    return redirect(url_for("view_period", period_id=period_id))


@app.route("/periods/<period_id>/days/<date>", methods=["POST"])
def update_day(period_id, date):
    if not store.period_exists(period_id):
        abort(404)
    period = store.load_period(period_id)

    nurse_a = request.form.get("nurse_a") or None
    nurse_b = request.form.get("nurse_b") or None
    locked = request.form.get("locked") == "on"

    found = False
    for a in period.assignments:
        if a.date == date:
            a.nurse_a = nurse_a
            a.nurse_b = nurse_b
            a.locked = locked
            found = True
            break
    if not found:
        abort(404)

    store.save_period(period)
    flash(f"{date}の担当者を保存しました。", "ok")
    return redirect(url_for("view_period", period_id=period_id))


@app.route("/periods/<period_id>/requests/grid", methods=["POST"])
def save_requests_grid(period_id):
    if not store.period_exists(period_id):
        abort(404)
    period = store.load_period(period_id)
    nurses = store.load_nurses()

    existing = {
        (r.nurse_id, r.date): r for r in period.shift_requests if r.type == "day_off_request"
    }
    other_requests = [r for r in period.shift_requests if r.type != "day_off_request"]

    new_requests = []
    for n in nurses.values():
        if not n.active:
            continue
        for a in period.assignments:
            value = request.form.get(f"req_{n.id}_{a.date}", "")
            if value in ("soft", "hard"):
                prev = existing.get((n.id, a.date))
                source = prev.source if prev else "manual"
                new_requests.append(
                    ShiftRequest(
                        nurse_id=n.id, date=a.date, type="day_off_request",
                        source=source, locked=(value == "hard"),
                    )
                )

    period.shift_requests = other_requests + new_requests
    store.save_period(period)
    flash(f"休み希望を保存しました（{len(new_requests)}件）。", "ok")
    return redirect(url_for("view_period", period_id=period_id))


@app.route("/periods/<period_id>/delete", methods=["POST"])
def delete_period(period_id):
    if not store.period_exists(period_id):
        abort(404)
    store.delete_period(period_id)
    flash(f"期間 {period_id} を削除しました。", "ok")
    return redirect(url_for("index"))


@app.route("/periods/<period_id>/export")
def export_period_route(period_id):
    if not store.period_exists(period_id):
        abort(404)
    period = store.load_period(period_id)
    nurses = store.load_nurses()
    buf = export_period(period, nurses)
    filename = f"シフト表_{period.start_date}_{period.end_date}.xlsx"
    return send_file(
        buf,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/nurses")
def list_nurses():
    nurses = store.load_nurses()
    ordered = list(nurses.values())  # 一覧の並び順は、登録順＋手動での並べ替えをそのまま反映する
    active_nurses = [n for n in nurses.values() if n.active]  # 一覧と同じ並び順（登録順＋並べ替え）
    pair_set = set(store.load_allowed_pairs())
    return render_template("nurses.html", nurses=ordered, active_nurses=active_nurses, pair_set=pair_set)


@app.route("/nurses", methods=["POST"])
def add_nurse():
    nurses = store.load_nurses()
    name = request.form.get("name", "").strip()
    role = request.form.get("role", "").strip()
    if not name:
        flash("氏名を入力してください。", "error")
        return redirect(url_for("list_nurses"))

    nid = store.next_nurse_id(nurses)
    nurses[nid] = Nurse(id=nid, name=name, role=role, active=True)
    store.save_nurses(nurses)
    flash(f"{name}さんを追加しました。", "ok")
    return redirect(url_for("list_nurses"))


@app.route("/nurses/pairs", methods=["POST"])
def save_pairs():
    nurses = store.load_nurses()
    active_nurses = [n for n in nurses.values() if n.active]  # 一覧と同じ並び順（登録順＋並べ替え）

    pairs = []
    for i, n1 in enumerate(active_nurses):
        for n2 in active_nurses[i + 1:]:
            if request.form.get(f"pair_{n1.id}_{n2.id}"):
                pairs.append((n1.id, n2.id))

    store.save_allowed_pairs(pairs)
    flash(f"組み合わせ設定を保存しました（{len(pairs)}件）。", "ok")
    return redirect(url_for("list_nurses"))


@app.route("/nurses/<nurse_id>/update", methods=["POST"])
def update_nurse(nurse_id):
    nurses = store.load_nurses()
    if nurse_id not in nurses:
        abort(404)

    name = request.form.get("name", "").strip()
    role = request.form.get("role", "").strip()
    if not name:
        flash("氏名を入力してください。", "error")
        return redirect(url_for("list_nurses"))

    raw_max_load = request.form.get("max_load", "").strip()
    max_load = None
    if raw_max_load != "":
        try:
            max_load = int(raw_max_load)
            if max_load < 0:
                raise ValueError
        except ValueError:
            flash("上限は0以上の整数で入力してください。", "error")
            return redirect(url_for("list_nurses"))

    nurse = nurses[nurse_id]
    nurse.name = name
    nurse.role = role
    nurse.max_load = max_load
    store.save_nurses(nurses)
    flash(f"{name}さんの情報を更新しました。", "ok")
    return redirect(url_for("list_nurses"))


@app.route("/nurses/<nurse_id>/move", methods=["POST"])
def move_nurse(nurse_id):
    nurses = store.load_nurses()
    ids = list(nurses.keys())
    if nurse_id not in ids:
        abort(404)

    direction = request.form.get("direction")
    idx = ids.index(nurse_id)
    if direction == "up" and idx > 0:
        ids[idx - 1], ids[idx] = ids[idx], ids[idx - 1]
    elif direction == "down" and idx < len(ids) - 1:
        ids[idx + 1], ids[idx] = ids[idx], ids[idx + 1]

    reordered = {i: nurses[i] for i in ids}
    store.save_nurses(reordered)
    return redirect(url_for("list_nurses"))


@app.route("/nurses/<nurse_id>/delete", methods=["POST"])
def delete_nurse(nurse_id):
    nurses = store.load_nurses()
    if nurse_id not in nurses:
        abort(404)

    name = nurses[nurse_id].name
    del nurses[nurse_id]
    store.save_nurses(nurses)

    # この人が入っている組み合わせ設定も一緒に取り除く
    pairs = store.load_allowed_pairs()
    remaining_pairs = [p for p in pairs if nurse_id not in p]
    if len(remaining_pairs) != len(pairs):
        store.save_allowed_pairs(remaining_pairs)

    flash(f"{name}さんを削除しました。", "ok")
    return redirect(url_for("list_nurses"))


@app.route("/nurses/<nurse_id>/toggle", methods=["POST"])
def toggle_nurse(nurse_id):
    nurses = store.load_nurses()
    if nurse_id in nurses:
        nurses[nurse_id].active = not nurses[nurse_id].active
        store.save_nurses(nurses)
    return redirect(url_for("list_nurses"))


if __name__ == "__main__":
    store.ensure_data_dirs()
    threading.Timer(1.2, lambda: webbrowser.open("http://127.0.0.1:5000")).start()
    app.run(host="127.0.0.1", port=5000, debug=False)

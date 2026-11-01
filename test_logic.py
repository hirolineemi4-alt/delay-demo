"""logic.py とサンプルCSVの検証。`python test_logic.py` で実行する。"""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

import pandas as pd

import generate_sample
import logic

ROOT = Path(__file__).resolve().parent
TODAY = date(2026, 11, 10)

EXPECTED = {
    "ORD-1010": ("高", 180, 3, "1/4", "遅延・着手遅れ・停滞・納期リスク"),
    "ORD-1003": ("高", 135, 4, "2/5", "遅延・着手遅れ・納期リスク"),
    "ORD-1019": ("高", 132, 5, "2/5", "遅延・納期リスク・保留"),
    "ORD-1015": ("高", 77, 6, "0/5", "納期リスク"),
    "ORD-1013": ("高", 65, 7, "3/4", "停滞"),
    "ORD-1005": ("中", 58, 9, "3/5", "遅延・保留"),
    "ORD-1025": ("中", 53, 8, "2/5", "納期リスク・保留"),
    "ORD-1017": ("中", 39, 20, "0/4", "遅延"),
    "ORD-1012": ("中", 31, 10, "1/4", "停滞"),
    "ORD-1022": ("中", 25, 16, "0/3", "保留"),
    "ORD-1008": ("低", 19, 12, "1/4", "着手遅れ"),
}


def test_sample_shape_and_encoding() -> None:
    path = ROOT / "sample_orders.csv"
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "UTF-8 BOM がありません"
    frame = logic.read_orders(path)
    assert list(frame.columns) == logic.REQUIRED_COLUMNS
    assert frame["order_id"].nunique() == 28
    assert len(frame) == 123
    assert len(generate_sample.build_rows()) == 123


def test_scores_on_reference_day() -> None:
    frame = logic.read_orders(ROOT / "sample_orders.csv")
    processes, orders = logic.analyze(frame, TODAY)
    assert len(orders) == 28
    attention = orders[orders["any_flag"]]
    assert list(attention["order_id"]) == list(EXPECTED)
    for order_id, (level, score, days, progress, rules) in EXPECTED.items():
        row = orders.loc[orders["order_id"] == order_id].iloc[0]
        assert row["importance"] == level, order_id
        assert int(row["score"]) == score, order_id
        assert int(row["days_until"]) == days, order_id
        assert row["progress"] == progress, order_id
        assert row["rules_label"] == rules, order_id
    clean = orders[~orders["any_flag"]]
    assert len(clean) == 17
    assert (clean["score"] == 0).all()
    assert (clean["rules_label"] == "問題なし").all()
    assert int(orders["any_flag"].sum()) == 11
    assert int((orders["importance"] == "高").sum()) == 5
    assert int(processes["is_stalled"].sum()) == 3
    assert int(orders["delayed"].sum()) == 5
    assert int(orders["start_delayed"].sum()) == 3
    assert int(orders["stalled"].sum()) == 3
    assert int(orders["due_risk"].sum()) == 5
    assert int(orders["on_hold"].sum()) == 4


def test_order_1010_reasons() -> None:
    frame = logic.read_orders(ROOT / "sample_orders.csv")
    _, orders = logic.analyze(frame, TODAY)
    reasons = orders.loc[orders["order_id"] == "ORD-1010", "reasons"].iloc[0]
    assert reasons == [
        "遅延：機械加工（予定完了11/6、4日超過）",
        "停滞：機械加工（5日間更新なし）",
        "遅延：表面処理（予定完了11/9、1日超過）",
        "着手遅れ：表面処理（予定開始11/7、未着手）",
        "納期リスク：残工程の予定日数10日 > 納期まで3日（7日不足）",
    ]
    assert orders.loc[orders["order_id"] == "ORD-1010", "current_process"].iloc[0] == "機械加工（着手中）"
    assert int(orders.loc[orders["order_id"] == "ORD-1010", "remaining_days"].iloc[0]) == 10


def test_urgent_forces_high_below_score_threshold() -> None:
    frame = logic.read_orders(ROOT / "sample_orders.csv")
    _, orders = logic.analyze(frame, TODAY)
    row = orders.loc[orders["order_id"] == "ORD-1015"].iloc[0]
    assert int(row["score"]) < 80
    assert bool(row["urgent"]) is True
    assert row["importance"] == "高"


def test_rule_based_report() -> None:
    frame = logic.read_orders(ROOT / "sample_orders.csv")
    _, orders = logic.analyze(frame, TODAY)
    report = logic.rule_based_report(orders, TODAY, urgent_days=7)
    assert "2026年11月10日" in report
    assert "要注意案件は11件" in report
    assert "高リスクは5件" in report
    assert "ORD-1010" in report
    assert "スコア180" in report
    assert "外注先に戻り日を確認する" in report
    assert "材料・外注・図面確認" in report


def test_missing_column_and_cp932() -> None:
    frame = logic.read_orders(ROOT / "sample_orders.csv").head(2)
    broken = frame.drop(columns=["due_date"])
    try:
        logic.read_orders(broken.to_csv(index=False).encode("utf-8"))
        raise AssertionError("必須列エラーになりませんでした")
    except ValueError as exc:
        assert "due_date" in str(exc)

    payload = frame.to_csv(index=False).encode("cp932")
    restored = logic.read_orders(payload)
    assert list(restored["order_id"]) == list(frame["order_id"])


def test_hold_keyword_without_status_and_past_due() -> None:
    today = TODAY
    hold = _single(
        status="着手中",
        note="図面確認中",
        planned_start="2026-11-10",
        planned_end="2026-11-12",
        due="2026-11-20",
        last_updated="2026-11-10",
    )
    _, orders = logic.analyze(hold, today)
    assert bool(orders.iloc[0]["on_hold"]) is True
    assert int(orders.iloc[0]["score"]) == 25
    assert orders.iloc[0]["importance"] == "中"

    late = _single(
        status="着手中",
        note="",
        planned_start="2026-11-09",
        planned_end="2026-11-09",
        due="2026-11-09",
        last_updated="2026-11-10",
        actual_start="2026-11-09",
    )
    _, late_orders = logic.analyze(late, today)
    # 遅延1日=33、残1日で納期を1日超過し不足2日=31、納期超過+30
    assert int(late_orders.iloc[0]["score"]) == 94
    assert bool(late_orders.iloc[0]["past_due"]) is True
    assert late_orders.iloc[0]["importance"] == "高"


def test_generator_matches_checked_in_csv() -> None:
    generated = pd.DataFrame(generate_sample.build_rows())
    checked_in = logic.read_orders(ROOT / "sample_orders.csv")
    generated["qty"] = generated["qty"].astype(int)
    for column in ("due_date", "planned_start", "planned_end", "actual_start", "actual_end", "last_updated"):
        generated[column] = pd.to_datetime(generated[column], errors="coerce")
    left = generated.sort_values(["order_id", "process_seq"]).reset_index(drop=True)
    right = checked_in.sort_values(["order_id", "process_seq"]).reset_index(drop=True)
    pd.testing.assert_frame_equal(left[logic.REQUIRED_COLUMNS], right[logic.REQUIRED_COLUMNS], check_dtype=False)
    with tempfile.TemporaryDirectory() as tmp:
        path = generate_sample.write_csv(Path(tmp) / "sample_orders.csv")
        assert path.read_bytes().startswith(b"\xef\xbb\xbf")


def _single(**kwargs) -> pd.DataFrame:
    row = {
        "order_id": "ORD-1",
        "customer": "テスト",
        "product": "部品",
        "qty": 1,
        "due_date": kwargs["due"],
        "process_seq": 1,
        "process_name": "加工",
        "planned_start": kwargs["planned_start"],
        "planned_end": kwargs["planned_end"],
        "actual_start": kwargs.get("actual_start", ""),
        "actual_end": kwargs.get("actual_end", ""),
        "status": kwargs["status"],
        "last_updated": kwargs["last_updated"],
        "note": kwargs["note"],
    }
    return logic.read_orders(pd.DataFrame([row]).to_csv(index=False).encode("utf-8"))


def main() -> None:
    test_sample_shape_and_encoding()
    test_scores_on_reference_day()
    test_order_1010_reasons()
    test_urgent_forces_high_below_score_threshold()
    test_rule_based_report()
    test_missing_column_and_cp932()
    test_hold_keyword_without_status_and_past_due()
    test_generator_matches_checked_in_csv()
    print("test_logic.py: 8 passed")


if __name__ == "__main__":
    main()

"""架空の工程表サンプルを作る。

正常17件は乱数シード42で生成し、基準日 2026-11-10 でフラグが出ないよう納期を調整する。
問題11件は手作り。出力は UTF-8 BOM 付き CSV（28受注・123工程行）。
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from pathlib import Path

TODAY = date(2026, 11, 10)
PROBLEM_IDS = {1003, 1005, 1008, 1010, 1012, 1013, 1015, 1017, 1019, 1022, 1025}

CUSTOMERS = [
    "株式会社テスト精工",
    "サンプル電機株式会社",
    "デモ産業株式会社",
    "ダミー自動車部品株式会社",
    "架空機械工業株式会社",
    "株式会社モック製作所",
]
PRODUCTS = [
    "ブラケット",
    "カバー",
    "スペーサー",
    "ピン",
    "ベース",
    "パネル",
    "ステー",
    "ブッシュ",
    "カラー",
    "プレート",
    "フレーム",
    "ガイド",
]
FLOWS = {
    4: [
        ["切断", "曲げ", "溶接", "検査"],
        ["切断", "機械加工", "検査", "出荷"],
        ["切断", "曲げ", "表面処理", "検査"],
    ],
    5: [
        ["切断", "曲げ", "溶接", "表面処理", "検査"],
        ["切断", "機械加工", "表面処理", "検査", "出荷"],
        ["切断", "曲げ", "溶接", "検査", "出荷"],
    ],
}

COLUMNS = [
    "order_id",
    "customer",
    "product",
    "qty",
    "due_date",
    "process_seq",
    "process_name",
    "planned_start",
    "planned_end",
    "actual_start",
    "actual_end",
    "status",
    "last_updated",
    "note",
]


def build_rows(today: date = TODAY) -> list[dict]:
    rows = _problem_rows()
    rng = random.Random(42)
    rows.extend(_normal_rows(rng, today))
    rows.sort(key=lambda row: (row["order_id"], row["process_seq"]))
    order_ids = {row["order_id"] for row in rows}
    if len(order_ids) != 28 or len(rows) != 123:
        raise RuntimeError(f"サンプル件数不一致: 受注{len(order_ids)} 行{len(rows)}")
    return rows


def write_csv(path: Path | None = None, today: date = TODAY) -> Path:
    destination = path or Path(__file__).resolve().parent / "sample_orders.csv"
    rows = build_rows(today)
    lines = [",".join(COLUMNS)]
    for row in rows:
        lines.append(",".join(_csv_field(row[column]) for column in COLUMNS))
    destination.write_bytes(("\ufeff" + "\n".join(lines) + "\n").encode("utf-8"))
    return destination


def _problem_rows() -> list[dict]:
    specs = [
        # 遅延・着手遅れ・停滞・納期リスク / スコア180
        dict(
            oid=1010,
            customer="ダミー自動車部品株式会社",
            product="シャフト",
            qty=50,
            due="2026-11-13",
            steps=[
                ("切断", "2026-11-01", "2026-11-01", "完了", "", "2026-11-01", "2026-11-01", ""),
                ("機械加工", "2026-11-02", "2026-11-06", "着手中", "", "2026-11-02", "", "2026-11-05"),
                ("表面処理", "2026-11-07", "2026-11-09", "未着手", "", "", "", "2026-11-01"),
                ("検査", "2026-11-10", "2026-11-11", "未着手", "", "", "", "2026-11-01"),
            ],
        ),
        # 遅延・着手遅れ・納期リスク / スコア135
        dict(
            oid=1003,
            customer="デモ産業株式会社",
            product="架台フレーム",
            qty=4,
            due="2026-11-14",
            steps=[
                ("切断", "2026-10-28", "2026-10-30", "完了", "", "2026-10-28", "2026-10-30", ""),
                ("曲げ", "2026-10-31", "2026-11-03", "完了", "", "2026-10-31", "2026-11-03", ""),
                ("溶接", "2026-11-04", "2026-11-07", "着手中", "歪み修正", "2026-11-04", "", "2026-11-09"),
                ("検査", "2026-11-08", "2026-11-10", "未着手", "", "", "", "2026-11-01"),
                ("出荷", "2026-11-11", "2026-11-11", "未着手", "", "", "", "2026-11-01"),
            ],
        ),
        # 遅延・納期リスク・保留 / スコア132
        dict(
            oid=1019,
            customer="サンプル電機株式会社",
            product="フランジ",
            qty=100,
            due="2026-11-15",
            steps=[
                ("切断", "2026-10-27", "2026-10-28", "完了", "", "2026-10-27", "2026-10-28", ""),
                ("機械加工", "2026-10-29", "2026-11-03", "完了", "", "2026-10-29", "2026-11-03", ""),
                ("表面処理", "2026-11-04", "2026-11-07", "保留", "メッキ外注待ち（戻り日未回答）", "", "", "2026-11-04"),
                ("検査", "2026-11-10", "2026-11-10", "未着手", "", "", "", "2026-11-01"),
                ("出荷", "2026-11-11", "2026-11-11", "未着手", "", "", "", "2026-11-01"),
            ],
        ),
        # 納期リスクのみ / スコア77（納期が近いので高）
        dict(
            oid=1015,
            customer="サンプル電機株式会社",
            product="制御盤筐体",
            qty=3,
            due="2026-11-16",
            steps=[
                ("切断", "2026-11-10", "2026-11-11", "未着手", "急ぎ追加受注。計画日数が納期を超えている", "", "", "2026-11-10"),
                ("曲げ", "2026-11-11", "2026-11-12", "未着手", "", "", "", "2026-11-10"),
                ("溶接", "2026-11-12", "2026-11-13", "未着手", "", "", "", "2026-11-10"),
                ("表面処理", "2026-11-13", "2026-11-14", "未着手", "", "", "", "2026-11-10"),
                ("検査", "2026-11-14", "2026-11-15", "未着手", "", "", "", "2026-11-10"),
            ],
        ),
        # 停滞のみ / スコア65（納期7日以内なので高）
        dict(
            oid=1013,
            customer="ダミー自動車部品株式会社",
            product="シャフト",
            qty=300,
            due="2026-11-17",
            steps=[
                ("切断", "2026-10-28", "2026-10-29", "完了", "", "2026-10-28", "2026-10-29", ""),
                ("機械加工", "2026-10-30", "2026-11-03", "完了", "", "2026-10-30", "2026-11-03", ""),
                ("表面処理", "2026-11-04", "2026-11-05", "完了", "", "2026-11-04", "2026-11-05", ""),
                ("検査", "2026-11-06", "2026-11-12", "着手中", "検査治具不具合", "2026-11-06", "", "2026-11-07"),
            ],
        ),
        # 遅延・保留 / スコア58
        dict(
            oid=1005,
            customer="架空機械工業株式会社",
            product="ベースプレート",
            qty=20,
            due="2026-11-19",
            steps=[
                ("切断", "2026-10-26", "2026-10-27", "完了", "", "2026-10-26", "2026-10-27", ""),
                ("曲げ", "2026-10-28", "2026-10-30", "完了", "", "2026-10-28", "2026-10-30", ""),
                ("溶接", "2026-10-31", "2026-11-04", "完了", "", "2026-10-31", "2026-11-04", ""),
                ("機械加工", "2026-11-06", "2026-11-09", "保留", "材料待ち（11/12入荷予定）", "", "", "2026-11-06"),
                ("検査", "2026-11-12", "2026-11-13", "未着手", "", "", "", "2026-11-06"),
            ],
        ),
        # 納期リスク・保留 / スコア53
        dict(
            oid=1025,
            customer="株式会社テスト精工",
            product="架台フレーム",
            qty=2,
            due="2026-11-18",
            steps=[
                ("切断", "2026-10-28", "2026-10-30", "完了", "", "2026-10-28", "2026-10-30", ""),
                ("曲げ", "2026-10-31", "2026-11-03", "完了", "", "2026-10-31", "2026-11-03", ""),
                ("溶接", "2026-11-08", "2026-11-12", "着手中", "溶接人員不足", "2026-11-08", "", "2026-11-10"),
                ("検査", "2026-11-13", "2026-11-14", "未着手", "", "", "", "2026-11-10"),
                ("出荷", "2026-11-15", "2026-11-16", "未着手", "", "", "", "2026-11-10"),
            ],
        ),
        # 遅延のみ / スコア39
        dict(
            oid=1017,
            customer="株式会社モック製作所",
            product="ブラケット",
            qty=500,
            due="2026-11-30",
            steps=[
                ("切断", "2026-11-04", "2026-11-07", "着手中", "再切断", "2026-11-04", "", "2026-11-09"),
                ("曲げ", "2026-11-12", "2026-11-14", "未着手", "", "", "", "2026-11-04"),
                ("溶接", "2026-11-15", "2026-11-17", "未着手", "", "", "", "2026-11-04"),
                ("検査", "2026-11-18", "2026-11-19", "未着手", "", "", "", "2026-11-04"),
            ],
        ),
        # 停滞のみ / スコア31
        dict(
            oid=1012,
            customer="株式会社モック製作所",
            product="治具プレート",
            qty=6,
            due="2026-11-20",
            steps=[
                ("切断", "2026-10-30", "2026-11-02", "完了", "", "2026-10-30", "2026-11-02", ""),
                ("機械加工", "2026-11-04", "2026-11-10", "着手中", "", "2026-11-04", "", "2026-11-05"),
                ("検査", "2026-11-11", "2026-11-12", "未着手", "", "", "", "2026-11-04"),
                ("出荷", "2026-11-13", "2026-11-13", "未着手", "", "", "", "2026-11-04"),
            ],
        ),
        # 保留のみ / スコア25
        dict(
            oid=1022,
            customer="デモ産業株式会社",
            product="カバー板金",
            qty=30,
            due="2026-11-26",
            steps=[
                ("切断", "2026-11-10", "2026-11-12", "保留", "図面確認中", "", "", "2026-11-10"),
                ("曲げ", "2026-11-13", "2026-11-14", "未着手", "", "", "", "2026-11-10"),
                ("検査", "2026-11-15", "2026-11-16", "未着手", "", "", "", "2026-11-10"),
            ],
        ),
        # 着手遅れのみ / スコア19
        dict(
            oid=1008,
            customer="株式会社テスト精工",
            product="ブラケット",
            qty=200,
            due="2026-11-22",
            steps=[
                ("切断", "2026-11-01", "2026-11-03", "完了", "", "2026-11-01", "2026-11-03", ""),
                ("曲げ", "2026-11-08", "2026-11-12", "未着手", "", "", "", "2026-11-01"),
                ("溶接", "2026-11-13", "2026-11-14", "未着手", "", "", "", "2026-11-01"),
                ("検査", "2026-11-15", "2026-11-16", "未着手", "", "", "", "2026-11-01"),
            ],
        ),
    ]
    rows: list[dict] = []
    for spec in specs:
        for seq, step in enumerate(spec["steps"], start=1):
            name, start, end, status, note, actual_start, actual_end, last_updated = step
            if status == "完了" and not last_updated:
                last_updated = actual_end or end
            rows.append(
                _row(
                    spec["oid"],
                    spec["customer"],
                    spec["product"],
                    spec["qty"],
                    spec["due"],
                    seq,
                    name,
                    start,
                    end,
                    status,
                    note,
                    actual_start,
                    actual_end,
                    last_updated or start,
                )
            )
    return rows


def _normal_rows(rng: random.Random, today: date) -> list[dict]:
    ids = [number for number in range(1001, 1029) if number not in PROBLEM_IDS]
    lengths = [5] * 7 + [4] * 10
    rng.shuffle(lengths)
    rows: list[dict] = []
    for number, length in zip(ids, lengths):
        rows.extend(_one_normal(number, length, rng, today))
    return rows


def _one_normal(number: int, length: int, rng: random.Random, today: date) -> list[dict]:
    customer = rng.choice(CUSTOMERS)
    product = rng.choice(PRODUCTS)
    qty = rng.randint(1, 400)
    flow = rng.choice(FLOWS[length])
    cursor = today - timedelta(days=rng.randint(0, 18))
    planned: list[tuple[str, date, date]] = []
    for name in flow:
        duration = rng.randint(1, 3)
        start = cursor
        end = cursor + timedelta(days=duration - 1)
        planned.append((name, start, end))
        cursor = end + timedelta(days=1)

    draft = []
    for name, start, end in planned:
        if end < today:
            status = "完了"
            actual_start, actual_end, last_updated = start, end, end
        elif start <= today:
            status = "着手中"
            actual_start, actual_end, last_updated = start, None, today
        else:
            status = "未着手"
            actual_start = actual_end = None
            last_updated = today
        draft.append((name, start, end, status, actual_start, actual_end, last_updated))

    remaining = 0
    for _, start, end, status, *_rest in draft:
        if status != "完了":
            remaining += (end - start).days + 1
    due = today + timedelta(days=remaining + rng.randint(3, 15))

    rows = []
    for seq, (name, start, end, status, actual_start, actual_end, last_updated) in enumerate(draft, start=1):
        rows.append(
            _row(
                number,
                customer,
                product,
                qty,
                due.isoformat(),
                seq,
                name,
                start.isoformat(),
                end.isoformat(),
                status,
                "",
                "" if actual_start is None else actual_start.isoformat(),
                "" if actual_end is None else actual_end.isoformat(),
                last_updated.isoformat(),
            )
        )
    return rows


def _row(
    number: int,
    customer: str,
    product: str,
    qty: int,
    due: str,
    seq: int,
    name: str,
    start: str,
    end: str,
    status: str,
    note: str,
    actual_start: str,
    actual_end: str,
    last_updated: str,
) -> dict:
    return {
        "order_id": f"ORD-{number:04d}",
        "customer": customer,
        "product": product,
        "qty": qty,
        "due_date": due,
        "process_seq": seq,
        "process_name": name,
        "planned_start": start,
        "planned_end": end,
        "actual_start": actual_start,
        "actual_end": actual_end,
        "status": status,
        "last_updated": last_updated,
        "note": note,
    }


def _csv_field(value) -> str:
    text = "" if value is None else str(value)
    if any(char in text for char in ",\"\n"):
        return '"' + text.replace('"', '""') + '"'
    return text


if __name__ == "__main__":
    output = write_csv()
    print(f"wrote {output}")

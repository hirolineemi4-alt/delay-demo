"""サンプルデータ生成スクリプト（架空データ）。固定シードで再現可能。
基準日 2026-11-10 時点の状況を想定。1行 = 1受注の1工程。
"""
import random
from datetime import date, timedelta
import pandas as pd

BASE = date(2026, 11, 10)
random.seed(42)

def d(n):
    return BASE + timedelta(days=n)

COLUMNS = ["order_id", "customer", "product", "qty", "due_date", "process_seq", "process_name",
           "planned_start", "planned_end", "actual_start", "actual_end", "status", "last_updated", "note",
           "material_arrival"]

CUSTOMERS = ["株式会社テスト精工", "サンプル電機株式会社", "デモ産業株式会社", "架空機械工業株式会社",
             "株式会社モック製作所", "ダミー自動車部品株式会社", "株式会社サンプル重工"]
ROUTES = {
    "ブラケット": ["切断", "曲げ", "溶接", "検査"],
    "架台フレーム": ["切断", "曲げ", "溶接", "表面処理", "検査"],
    "シャフト": ["切断", "機械加工", "表面処理", "検査"],
    "カバー板金": ["切断", "曲げ", "検査"],
    "制御盤筐体": ["切断", "曲げ", "溶接", "表面処理", "検査"],
    "治具プレート": ["切断", "機械加工", "検査"],
    "フランジ": ["切断", "機械加工", "表面処理", "検査", "出荷"],
    "ベースプレート": ["切断", "機械加工", "検査", "出荷"],
}

rows = []

def add(oid, cust, prod, qty, due, procs, material_arrival=None):
    """procs: (name, ps, pe, status, as, ae, lu, note)  日付はBASEからの日数オフセット or None。
    material_arrival は先頭工程の材料入荷日（BASEからの日数）。着手予定より後なら前兆ルール5。"""
    for i, (name, ps, pe, st, a_s, a_e, lu, note) in enumerate(procs, 1):
        rows.append(dict(order_id=oid, customer=cust, product=prod, qty=qty, due_date=d(due),
                         process_seq=i, process_name=name, planned_start=d(ps), planned_end=d(pe),
                         actual_start=d(a_s) if a_s is not None else None,
                         actual_end=d(a_e) if a_e is not None else None,
                         status=st, last_updated=d(lu), note=note,
                         material_arrival=d(material_arrival) if (material_arrival is not None and i == 1) else None))

# ---------------- 手作りの問題ケース ----------------
problem = {}
problem["ORD-1003"] = lambda: add("ORD-1003", "デモ産業株式会社", "架台フレーム", 4, 4, [
    ("切断", -10, -9, "完了", -10, -9, -9, ""),
    # 実績完了 11/06 は予定完了 11/03 より3日遅い → 計画と実績のズレ2日超
    ("曲げ", -8, -7, "完了", -8, -4, -4, "曲げ角度の再調整あり（実績完了が予定より3日遅れ）"),
    ("溶接", -6, -3, "着手中", -4, None, -1, "溶接歪みの修正に時間がかかっている"),
    ("表面処理", -2, 0, "未着手", None, None, -6, ""),
    ("検査", 1, 1, "未着手", None, None, -6, "")])
problem["ORD-1010"] = lambda: add("ORD-1010", "ダミー自動車部品株式会社", "シャフト", 50, 3, [
    ("切断", -9, -9, "完了", -9, -9, -9, ""),
    ("機械加工", -8, -4, "着手中", -8, None, -5, ""),
    ("表面処理", -3, -1, "未着手", None, None, -9, ""),
    ("検査", 0, 1, "未着手", None, None, -9, "")])
problem["ORD-1008"] = lambda: add("ORD-1008", "株式会社テスト精工", "ブラケット", 200, 12, [
    ("切断", -6, -5, "完了", -6, -5, -5, ""),
    ("曲げ", -2, 1, "未着手", None, None, -5, ""),
    ("溶接", 2, 4, "未着手", None, None, -5, ""),
    ("検査", 5, 5, "未着手", None, None, -5, "")])
problem["ORD-1012"] = lambda: add("ORD-1012", "株式会社モック製作所", "治具プレート", 6, 10, [
    ("切断", -7, -7, "完了", -7, -7, -7, ""),
    ("機械加工", -5, 1, "着手中", -5, None, -5, ""),
    ("表面処理", 2, 3, "未着手", None, None, -7, ""),
    ("検査", 4, 4, "未着手", None, None, -7, "")])
problem["ORD-1015"] = lambda: add("ORD-1015", "サンプル電機株式会社", "制御盤筐体", 3, 6, [
    ("切断", 1, 2, "未着手", None, None, -1, "急ぎ案件として追加受注"),
    ("曲げ", 3, 4, "未着手", None, None, -1, ""),
    ("溶接", 5, 7, "未着手", None, None, -1, ""),
    ("表面処理", 8, 9, "未着手", None, None, -1, ""),
    ("検査", 10, 10, "未着手", None, None, -1, "")])
problem["ORD-1005"] = lambda: add("ORD-1005", "架空機械工業株式会社", "ベースプレート", 20, 9, [
    # 材料入荷 11/12 は切断の着手予定 11/08 より後 → 材料入荷が着手予定より後
    ("切断", -2, -1, "保留", None, None, -3, "材料待ち（SS400 t12 入荷遅れ、11/12入荷予定）"),
    ("機械加工", 0, 3, "未着手", None, None, -3, ""),
    ("検査", 4, 5, "未着手", None, None, -3, ""),
    ("出荷", 6, 6, "未着手", None, None, -3, "")], material_arrival=2)
problem["ORD-1019"] = lambda: add("ORD-1019", "サンプル電機株式会社", "フランジ", 100, 5, [
    ("切断", -12, -11, "完了", -12, -11, -11, ""),
    ("機械加工", -10, -6, "完了", -10, -6, -6, ""),
    ("表面処理", -5, -1, "保留", -5, None, -4, "外注待ち（メッキ外注先から戻り日の回答なし）"),
    ("検査", 0, 1, "未着手", None, None, -6, ""),
    ("出荷", 2, 2, "未着手", None, None, -6, "")])
problem["ORD-1022"] = lambda: add("ORD-1022", "デモ産業株式会社", "カバー板金", 30, 16, [
    ("切断", 1, 2, "保留", None, None, -2, "図面確認中（改訂図の寸法を顧客へ問い合わせ中）"),
    ("曲げ", 3, 4, "未着手", None, None, -2, ""),
    ("検査", 5, 5, "未着手", None, None, -2, "")])
problem["ORD-1025"] = lambda: add("ORD-1025", "株式会社テスト精工", "架台フレーム", 2, 8, [
    ("切断", -6, -5, "完了", -6, -5, -5, ""),
    ("曲げ", -4, -4, "完了", -4, -4, -4, ""),
    ("溶接", -3, 1, "着手中", -3, None, -1, "溶接人員不足（担当者1名が休暇）"),
    ("表面処理", 2, 4, "未着手", None, None, -5, ""),
    ("検査", 5, 5, "未着手", None, None, -5, "")])
problem["ORD-1017"] = lambda: add("ORD-1017", "株式会社モック製作所", "ブラケット", 500, 20, [
    ("切断", -5, -3, "着手中", -5, None, 0, "材料の歩留まりが悪く一部再切断"),
    ("曲げ", 0, 2, "未着手", None, None, -6, ""),
    ("溶接", 3, 5, "未着手", None, None, -6, ""),
    ("検査", 6, 6, "未着手", None, None, -6, "")])
problem["ORD-1013"] = lambda: add("ORD-1013", "ダミー自動車部品株式会社", "シャフト", 300, 7, [
    ("切断", -10, -10, "完了", -10, -10, -10, ""),
    ("機械加工", -9, -6, "完了", -9, -6, -6, ""),
    ("表面処理", -5, -4, "完了", -5, -4, -4, ""),
    ("検査", -3, 1, "着手中", -3, None, -3, "検査治具の不具合で一時中断"),
    ("出荷", 2, 2, "未着手", None, None, -4, "")])

# ---------------- 正常案件（ランダム生成） ----------------
def normal_order(oid):
    prod = random.choice(list(ROUTES))
    route = ROUTES[prod]
    cust = random.choice(CUSTOMERS)
    qty = random.choice([2, 5, 10, 20, 30, 50, 100, 150, 300])
    start = random.randint(-14, 5)
    plan, cur = [], start
    for name in route:
        dur = random.randint(1, 3) if name not in ("検査", "出荷") else 1
        plan.append((name, cur, cur + dur - 1))
        cur += dur + random.randint(0, 1)
    due = plan[-1][2] + random.randint(2, 5)
    procs = []
    for name, ps, pe in plan:
        if pe < 0:  # 予定どおり完了済み
            a_s = ps - random.randint(0, 1) if ps > -14 else ps
            a_e = pe - random.randint(0, 1) if pe - 1 >= a_s else pe
            procs.append((name, ps, pe, "完了", a_s, a_e, a_e, ""))
        elif ps < 0 or (ps == 0 and random.random() < 0.6):  # 予定どおり着手中
            procs.append((name, ps, pe, "着手中", ps, None, -random.randint(0, 1), ""))
        else:
            procs.append((name, ps, pe, "未着手", None, None, min(start, 0) - 3, ""))
    # 納期リスクが出ないよう納期を調整
    remaining = sum(pe - ps + 1 for (n, ps, pe, st, *_ ) in procs if st != "完了")
    due = max(due, remaining + 1)
    if all(p[3] == "完了" for p in procs):
        due = max(due, 1)
    add(oid, cust, prod, qty, due, procs)

ids = [f"ORD-{i}" for i in range(1001, 1029)]
for oid in ids:
    if oid in problem:
        problem[oid]()
    else:
        normal_order(oid)

df = pd.DataFrame(rows, columns=COLUMNS)
for c in ["due_date", "planned_start", "planned_end", "actual_start", "actual_end", "last_updated",
          "material_arrival"]:
    df[c] = pd.to_datetime(df[c]).dt.strftime("%Y-%m-%d")
df = df.fillna("")
df.to_csv("sample_orders.csv", index=False, encoding="utf-8-sig")
print(f"{df.order_id.nunique()} orders, {len(df)} rows -> sample_orders.csv")

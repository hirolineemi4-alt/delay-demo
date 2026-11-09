import io
from datetime import date
import pandas as pd
from logic import (read_csv, analyze, Thresholds, rule_based_report, build_llm_prompt, FLAG_ORDER,
                   PRECURSOR_ORDER, morning_three, SLIP_DAYS)
th = Thresholds()
df = read_csv("sample_orders.csv")
p, o = analyze(df, date(2026, 11, 10), th)
print("orders", len(o), "rows", len(df))
print("process-level:", {f: int(p[f"f_{f}"].sum()) for f in ["遅延","着手遅れ","停滞","保留"]})
print("order-level:", {f: int(o[f"f_{f}"].sum()) for f in FLAG_ORDER})
print("precursors:", {f: int(o[f"f_{f}"].sum()) for f in PRECURSOR_ORDER})
print(o["rank"].value_counts().to_dict())
print(o[o.issues!="問題なし"][["order_id","days_to_due","score","rank","issues","precursors","impact_days"]].to_string())
# 正常案件にフラグが無いこと
problem = {"ORD-1003","ORD-1010","ORD-1008","ORD-1012","ORD-1015","ORD-1005","ORD-1019","ORD-1022","ORD-1025","ORD-1017","ORD-1013"}
flagged = set(o[o.issues!="問題なし"].order_id)
assert flagged == problem, (flagged ^ problem)
# 整合性: 完了行は実績日あり
d = df[df.status=="完了"]; assert d.actual_start.notna().all() and d.actual_end.notna().all()
assert df[df.status=="未着手"].actual_start.isna().all()
rep = rule_based_report(o, date(2026,11,10), th)
assert "トップ5" in rep and "今朝見る3件" in rep and "納期への影響" in rep
prompt = build_llm_prompt(o, date(2026,11,10))
assert "前兆" in prompt and "納期への影響日数" in prompt

# 前兆5ルールは、それぞれ架空サンプルの1件以上に当たる。APIキーは不要。
assert SLIP_DAYS == 2
shown = {
    "未着手の超過": "ORD-1008",          # 曲げの着手予定 11/08 を過ぎても未着手
    "状態がN日変わらない停滞": "ORD-1010",  # 機械加工が着手中のまま 11/05 から5日更新なし
    "計画と実績のズレ2日超": "ORD-1003",    # 曲げの実績完了が予定より3日遅い
    "完了見込みが納期超過": "ORD-1010",     # 残工程を足すと納期 11/13 を超える
    "材料入荷が着手予定より後": "ORD-1005",  # 入荷 11/12 が切断の着手予定 11/08 より後
}
for name, oid in shown.items():
    hit = set(o.loc[o[f"f_{name}"], "order_id"])
    assert hit, name
    assert oid in hit, (name, oid, hit)
top = morning_three(o)
assert len(top) == 3, top
assert top.iloc[0].order_id == "ORD-1010"
assert set(["impact_days", "forecast_date", "precursors"]).issubset(top.columns)
assert int(top.iloc[0].impact_days) > 0
# 材料列が無い旧CSVでも読める。その場合ルール5は立たない。
bare = pd.read_csv("sample_orders.csv", encoding="utf-8-sig").drop(columns=["material_arrival"])
bare_df = read_csv(io.BytesIO(bare.to_csv(index=False).encode("utf-8-sig")))
_, bare_o = analyze(bare_df, date(2026, 11, 10), th)
assert not bare_o["f_材料入荷が着手予定より後"].any()

# ズレは「2日超」。ちょうど2日は対象外、3日は対象。
def _one(slip_end, material=None, status="完了"):
    row = dict(order_id="ORD-T", customer="株式会社テスト精工", product="試作", qty=1,
               due_date="2026-11-20", process_seq=1, process_name="切断",
               planned_start="2026-11-01", planned_end="2026-11-03",
               actual_start="2026-11-01", actual_end="2026-11-03",
               status=status, last_updated="2026-11-03", note="", material_arrival=material or "")
    end = pd.Timestamp("2026-11-03") + pd.Timedelta(days=slip_end)
    row["actual_end"] = f"{end:%Y-%m-%d}"
    buf = io.BytesIO(pd.DataFrame([row]).to_csv(index=False).encode())
    return analyze(read_csv(buf), date(2026, 11, 10), th)[1].iloc[0]
assert not bool(_one(2)["f_計画と実績のズレ2日超"])
assert bool(_one(3)["f_計画と実績のズレ2日超"])
late_mat = _one(0, material="2026-11-05", status="未着手")
assert bool(late_mat["f_材料入荷が着手予定より後"])
assert not bool(_one(0, material="2026-11-01", status="未着手")["f_材料入荷が着手予定より後"])
print("ALL TESTS PASSED")

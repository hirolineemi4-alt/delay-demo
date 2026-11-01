from datetime import date
from logic import read_csv, analyze, Thresholds, rule_based_report, build_llm_prompt, FLAG_ORDER
th = Thresholds()
df = read_csv("sample_orders.csv")
p, o = analyze(df, date(2026, 11, 10), th)
print("orders", len(o), "rows", len(df))
print("process-level:", {f: int(p[f"f_{f}"].sum()) for f in ["遅延","着手遅れ","停滞","保留"]})
print("order-level:", {f: int(o[f"f_{f}"].sum()) for f in FLAG_ORDER})
print(o["rank"].value_counts().to_dict())
print(o[o.issues!="問題なし"][["order_id","days_to_due","score","rank","issues"]].to_string())
# 正常案件にフラグが無いこと
problem = {"ORD-1003","ORD-1010","ORD-1008","ORD-1012","ORD-1015","ORD-1005","ORD-1019","ORD-1022","ORD-1025","ORD-1017","ORD-1013"}
flagged = set(o[o.issues!="問題なし"].order_id)
assert flagged == problem, (flagged ^ problem)
# 整合性: 完了行は実績日あり
d = df[df.status=="完了"]; assert d.actual_start.notna().all() and d.actual_end.notna().all()
assert df[df.status=="未着手"].actual_start.isna().all()
rep = rule_based_report(o, date(2026,11,10), th); assert "トップ5" in rep
build_llm_prompt(o, date(2026,11,10))
print("ALL TESTS PASSED")

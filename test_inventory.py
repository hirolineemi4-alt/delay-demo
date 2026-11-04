"""在庫の見える化デモの検証（ロジック＋列名ゆれ＋画面）。"""
import io
import pandas as pd
from streamlit.testing.v1 import AppTest
from inventory_logic import read_inventory, analyze_inventory, kpis, InvThresholds, inventory_rule_report, build_inventory_prompt

th = InvThresholds()
df, info = read_inventory("sample_inventory.xlsx")
assert 30 <= len(df) <= 60, len(df)
d = analyze_inventory(df, th)
k = kpis(d)
print("status:", d["status"].value_counts().to_dict(), "kpi:", k)
assert k["danger"] == 6 and k["risk"] == 10 and k["over"] == 9 and k["dead"] == 2, k
assert k["excess_value"] > 0
assert "欠品リスク" in inventory_rule_report(d, th) and build_inventory_prompt(d, th)
# CSVも同じ結果
d_csv = analyze_inventory(read_inventory("sample_inventory.csv")[0], th)
assert (d_csv["status"].values == d["status"].values).all()

# 列名ゆれ・タイトル行・合計行・カンマ付き数値・Shift_JIS・直近3ヶ月出荷・単価なし
raw = pd.read_csv("sample_inventory.csv", encoding="utf-8-sig")
v = raw.rename(columns={"品番": "品目コード", "品名": "品目名", "現在庫": "在庫数（個）", "発注点": "安全在庫",
                        "入荷予定": "発注残"}).drop(columns=["単価"])
v["直近3ヶ月出荷"] = v.pop("月平均出荷") * 3
v["在庫数（個）"] = v["在庫数（個）"].map(lambda x: f"{x:,}")
v = v.astype(object)
v.loc[len(v)] = ["", "合計", "", "", "", ""]
buf = io.StringIO()
buf.write("在庫一覧表（2026年11月）,,,,,\n,,,,,\n")
v.to_csv(buf, index=False)
dv, iv = read_inventory(io.BytesIO(buf.getvalue().encode("cp932")), "zaiko.csv")
assert iv["header_row"] == 3 and iv["recent_months"] == 3 and not iv["has_price"], iv
dv = analyze_inventory(dv, th)
assert len(dv) == len(d) and kpis(dv)["excess_value"] is None
assert sorted(dv["status"]) == sorted(d["status"]), "列名ゆれで結果が変わった"
# 英語列名・日平均
e = raw.rename(columns={"品番": "Part No", "品名": "Description", "現在庫": "On Hand", "入荷予定": "On Order",
                        "単価": "Unit Cost", "発注点": "Reorder Point"})
e["Daily Usage"] = e.pop("月平均出荷") / 30
de, ie = read_inventory(io.BytesIO(e.to_csv(index=False).encode()), "en.csv")
assert ie["has_price"] and len(de) == len(d), ie
# 必須列なし → 分かりやすいエラー
try:
    read_inventory(io.BytesIO("品番,品名\nA,B\n".encode()), "bad.csv")
    raise AssertionError("should fail")
except ValueError as ex:
    assert "現在庫" in str(ex)
print("logic ok")

# ---- 画面（ルーター経由で /inventory を開く）----
at = AppTest.from_file("app.py", default_timeout=60).run()
assert not at.exception, at.exception
assert at.title[0].value.startswith("🏭"), at.title[0].value  # ルートは従来の納期遅れデモ
at.switch_page("inventory_demo.py").run()
assert not at.exception, at.exception
assert at.title[0].value.startswith("📦"), at.title[0].value
body = " ".join(m.value for m in at.markdown)
assert "お手元の在庫表をそのまま読み込むだけで" in body
assert "欠品リスク" in body and "10品目" in body and "過剰在庫" in body, body[:500]
assert len(at.dataframe) == 3, len(at.dataframe)
at.slider[0].set_value(30).run(); assert not at.exception, at.exception
at.slider[1].set_value(6.0).run(); assert not at.exception, at.exception
print("INVENTORY APP TEST PASSED")

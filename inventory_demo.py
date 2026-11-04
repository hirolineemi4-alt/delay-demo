"""在庫の見える化デモ（Streamlit）— URL：/inventory"""
import io
from pathlib import Path
import pandas as pd
import streamlit as st
from inventory_logic import (InvThresholds, read_inventory, analyze_inventory, kpis, yen,
                             inventory_rule_report, build_inventory_prompt, STATUS_ICON, DAYS_PER_MONTH)
from logic import llm_provider, llm_report

APP_DIR = Path(__file__).parent
SAMPLE_XLSX = APP_DIR / "sample_inventory.xlsx"

st.set_page_config(page_title="在庫の見える化デモ", page_icon="📦", layout="centered")
st.markdown("""
<style>
.block-container {padding-top: 1.6rem; max-width: 980px;}
.kpi {background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:12px 16px;box-shadow:0 1px 3px rgba(0,0,0,.05);margin-bottom:8px;}
.kpi .label {color:#6b7280;font-size:.85rem;}
.kpi .value {font-size:1.8rem;font-weight:700;line-height:1.2;}
.kpi .sub {color:#9ca3af;font-size:.75rem;}
.badge {display:inline-block;padding:2px 10px;border-radius:999px;font-size:.8rem;font-weight:600;margin-right:4px;}
</style>""", unsafe_allow_html=True)

st.title("📦 在庫の見える化デモ")
st.markdown("**お手元の在庫表をそのまま読み込むだけで、欠品しそうな品と在庫が多すぎる品を一覧にします。**")
st.caption("マスタ登録は不要です。Excel／CSVの列名（品番・品名・現在庫・月平均出荷 など）を自動で読み取ります。")

# ---------------- 読み込み・設定（スマホでも見えるよう本文に配置） ----------------
up = st.file_uploader("在庫表（Excel / CSV）をアップロード", type=["xlsx", "xlsm", "csv"],
                      help="未指定の場合はサンプルデータ（架空）を使用します")
c_dl, c_note = st.columns([1, 2])
c_dl.download_button("⬇️ サンプル在庫表（Excel）", SAMPLE_XLSX.read_bytes(), file_name="sample_inventory.xlsx",
                     mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
c_note.caption("サンプルは架空の町工場の在庫表（48品目）です。列名が多少違っても読み込めます。"
               "必要なのは「品番か品名」「現在庫」「月平均出荷（または直近の出荷数）」の3つだけです。")

with st.expander("⚙️ 判定のしきい値を調整"):
    shortage_days = st.slider("欠品リスク：在庫が何日分以下なら", 3, 60, 14, step=1)
    overstock_months = st.slider("過剰在庫：在庫が何か月分を超えたら", 1.0, 12.0, 3.0, step=0.5)
    st.caption(f"在庫日数 ＝ 現在庫 ÷（月平均出荷 ÷ {DAYS_PER_MONTH}日）。"
               "入荷予定を含めても足りない品は「欠品危険」、入荷予定が間に合えば足りる品と発注点を割った品は「欠品注意」。"
               "過剰在庫の月数は入荷予定を含めて計算します。")
th = InvThresholds(shortage_days=int(shortage_days), overstock_months=float(overstock_months))

try:
    if up is not None:
        df, info = read_inventory(io.BytesIO(up.getvalue()), up.name)
    else:
        df, info = read_inventory(SAMPLE_XLSX)
except Exception as e:
    st.error(f"在庫表の読み込みに失敗しました：{e}")
    st.stop()
src_name = up.name if up is not None else "sample_inventory.xlsx（架空のサンプルデータ）"

d = analyze_inventory(df, th)
k = kpis(d)

# ---------------- KPI ----------------
def kpi(col, label, value, sub, color):
    col.markdown(f'<div class="kpi"><div class="label">{label}</div>'
                 f'<div class="value" style="color:{color}">{value}</div><div class="sub">{sub}</div></div>',
                 unsafe_allow_html=True)

c1, c2, c3 = st.columns(3)
kpi(c1, "欠品リスク", f"{k['risk']}品目", f"うち危険 {k['danger']}／{th.shortage_days}日分以下", "#dc2626")
kpi(c2, "過剰在庫", f"{k['over']}品目", f"{th.overstock_months:g}か月分超（出荷なし {k['dead']}含む）", "#2563eb")
if k["excess_value"] is not None:
    kpi(c3, "過剰在庫の金額（目安）", yen(k["excess_value"]), f"在庫金額合計 {yen(k['stock_value'])}", "#7c3aed")
else:
    kpi(c3, "全品目数", f"{k['items']}品目", "単価列があれば過剰在庫の金額を表示", "#111827")
mapped = "、".join(f"{v}→{key}" if v != key else key for key, v in info["mapping"].items())
st.caption(f"データ：{src_name}（{info['rows']}品目）　／　読み取った列：{mapped}")
if info.get("recent_months"):
    st.caption(f"※「{info['mapping']['直近の出荷数']}」を{info['recent_months']}か月分の出荷とみなし、月平均に換算しました。")

# ---------------- 表 ----------------
STATUS_COLOR = {"欠品危険": "#fde2e2", "欠品注意": "#fff4d6", "過剰在庫": "#dbeafe", "動きなし": "#f3f4f6", "適正": "#ffffff"}


def display_table(x: pd.DataFrame) -> pd.DataFrame:
    t = pd.DataFrame({
        "判定": x["status"].map(STATUS_ICON), "品番": x["code"], "品名": x["name"],
        "現在庫": x["stock"], "月平均出荷": x["monthly"],
        "在庫日数": x["cover_days"].round(0), "在庫月数(入荷込)": x["cover_months"].round(1)})
    if info["has_incoming"]:
        t.insert(4, "入荷予定", x["incoming"])
    if info["has_reorder"]:
        t.insert(5 if info["has_incoming"] else 4, "発注点", pd.to_numeric(x["reorder"], errors="coerce"))
    if info["has_price"]:
        t["過剰分の金額"] = x["excess_value"].round(0).where(x["excess_value"] > 0)
    t["理由"] = x["reason"]
    return t


def show(x: pd.DataFrame, key: str):
    if x.empty:
        st.success("該当する品目はありません。")
        return
    t = display_table(x)
    styled = t.style.apply(lambda r: [f"background-color:{STATUS_COLOR[x.loc[r.name, 'status']]}"] * len(r), axis=1)
    st.dataframe(styled, width="stretch", hide_index=True, height=min(80 + 35 * len(t), 560), key=key,
                 column_config={
                     "現在庫": st.column_config.NumberColumn(format="%d"),
                     "月平均出荷": st.column_config.NumberColumn(format="%d"),
                     "入荷予定": st.column_config.NumberColumn(format="%d"),
                     "発注点": st.column_config.NumberColumn(format="%d"),
                     "在庫日数": st.column_config.NumberColumn(format="%d日", help="現在庫だけで何日もつか（出荷なしは空欄）"),
                     "在庫月数(入荷込)": st.column_config.NumberColumn(format="%.1fか月"),
                     "過剰分の金額": st.column_config.NumberColumn(format="¥%d", help=f"{th.overstock_months:g}か月分を超える手元在庫 × 単価"),
                     "理由": st.column_config.TextColumn(width="medium")})
    st.caption("列名をタップ（クリック）すると並べ替えできます。")


risk = d[d["status"].isin(["欠品危険", "欠品注意"])]
over = d[d["status"].isin(["過剰在庫", "動きなし"])]
if info["has_price"]:
    over = over.sort_values("excess_value", ascending=False)

tab1, tab2, tab3, tab4 = st.tabs([f"🔴 欠品リスク（{len(risk)}）", f"🔵 過剰在庫（{len(over)}）", "📋 全品目", "📝 AIレポート"])
with tab1:
    show(risk, "tbl_risk")
with tab2:
    show(over, "tbl_over")
with tab3:
    show(d, "tbl_all")
    out = display_table(d)
    st.download_button("⬇️ 判定結果をCSVでダウンロード", out.to_csv(index=False).encode("utf-8-sig"),
                       file_name="在庫判定結果.csv", mime="text/csv")

with tab4:
    prov = llm_provider()
    if prov:
        st.info(f"AIプロバイダー：{prov[0]}（モデル：{prov[3]}）。ボタンを押すとAIがレポートを作成します。")
        if st.button("🤖 AIでレポートを生成", type="primary"):
            with st.spinner("AIがレポートを作成中…"):
                try:
                    st.session_state["inv_ai_report"] = llm_report(build_inventory_prompt(d, th))
                except Exception as e:
                    st.session_state["inv_ai_report"] = None
                    st.error(f"AI呼び出しに失敗したため、ルールベースのレポートを表示します（{e}）")
        if st.session_state.get("inv_ai_report"):
            st.markdown('<span class="badge" style="background:#dbeafe;color:#1e40af">AI生成</span>', unsafe_allow_html=True)
            report = st.session_state["inv_ai_report"]
        else:
            st.markdown('<span class="badge" style="background:#e5e7eb;color:#374151">ルールベース生成</span>', unsafe_allow_html=True)
            report = inventory_rule_report(d, th)
    else:
        st.markdown('<span class="badge" style="background:#e5e7eb;color:#374151">ルールベース生成</span>'
                    '<span style="color:#6b7280;font-size:.85rem">（OPENAI_API_KEY / XAI_API_KEY を設定するとAIが文章を作成します）</span>',
                    unsafe_allow_html=True)
        report = inventory_rule_report(d, th)
    with st.container(border=True):
        st.markdown(report)
    st.download_button("⬇️ レポートをテキストで保存", report.encode("utf-8"),
                       file_name="在庫の見える化レポート.md", mime="text/markdown")

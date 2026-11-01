"""納期遅れ・停滞の早期検知デモ（Streamlit）"""
from datetime import date, timedelta
from pathlib import Path
import pandas as pd
import plotly.express as px
import streamlit as st
from logic import (Thresholds, read_csv, analyze, rule_based_report, build_llm_prompt,
                   llm_provider, llm_report, FLAG_ORDER)

APP_DIR = Path(__file__).parent
SAMPLE = APP_DIR / "sample_orders.csv"

st.set_page_config(page_title="納期遅れ・停滞 早期検知デモ", page_icon="🏭", layout="wide")
st.markdown("""
<style>
.block-container {padding-top: 1.6rem;}
.kpi {background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:14px 18px;box-shadow:0 1px 3px rgba(0,0,0,.05);}
.kpi .label {color:#6b7280;font-size:.85rem;}
.kpi .value {font-size:2rem;font-weight:700;line-height:1.2;}
.kpi .sub {color:#9ca3af;font-size:.75rem;}
.badge {display:inline-block;padding:2px 10px;border-radius:999px;font-size:.8rem;font-weight:600;margin-right:4px;}
</style>""", unsafe_allow_html=True)

# ---------------- サイドバー ----------------
with st.sidebar:
    st.header("⚙️ 設定")
    up = st.file_uploader("工程データCSVをアップロード", type=["csv"],
                          help="未指定の場合はサンプルデータ（架空）を使用します")
    base_date = st.date_input("基準日（今日）", value=date(2026, 11, 10))
    st.subheader("検知ルールのしきい値")
    stall_days = st.number_input("停滞：最終更新からの日数 ≥", 1, 30, 3)
    due_window = st.number_input("納期が近い：納期まで何日以内", 1, 60, 7)
    with st.expander("重要度スコアの境界"):
        high_score = st.number_input("「高」スコア ≥", 10, 500, 80)
        mid_score = st.number_input("「中」スコア ≥", 1, 500, 25)
    st.caption("ルール：遅延／着手遅れ／停滞／納期リスク／保留\n\n"
               f"納期{due_window}日以内かつ何らかの問題がある案件は自動的に「高」になります。")

th = Thresholds(stall_days=int(stall_days), due_window=int(due_window),
                high_score=int(high_score), mid_score=int(mid_score))

st.title("🏭 納期遅れ・停滞の早期検知デモ")
st.caption("CSV読み込み → ルールで遅れ・停滞を抽出 → 重要度順に一覧 → 「今週手を打つべき案件」をレポート化")

try:
    df = read_csv(up) if up is not None else read_csv(SAMPLE)
except Exception as e:
    st.error(f"CSVの読み込みに失敗しました：{e}")
    st.stop()
src_name = up.name if up is not None else "sample_orders.csv（架空のサンプルデータ）"

procs, orders = analyze(df, base_date, th)
flagged = orders[orders["issues"] != "問題なし"]

# ---------------- KPI ----------------
def kpi(col, label, value, sub, color):
    col.markdown(f'<div class="kpi"><div class="label">{label}</div>'
                 f'<div class="value" style="color:{color}">{value}</div><div class="sub">{sub}</div></div>',
                 unsafe_allow_html=True)

c1, c2, c3, c4 = st.columns(4)
kpi(c1, "全案件数", len(orders), f"工程行数 {len(df)}", "#111827")
kpi(c2, "要注意案件数", len(flagged), "いずれかのルールに該当", "#d97706")
kpi(c3, "高リスク件数", int((orders["rank"] == "高").sum()), f"納期{th.due_window}日以内の問題案件など", "#dc2626")
kpi(c4, "停滞工程数", int(procs["f_停滞"].sum()), f"{th.stall_days}日以上更新なし（着手中）", "#7c3aed")
st.caption(f"データ：{src_name}　／　基準日：{base_date:%Y-%m-%d}")

tab1, tab2, tab3 = st.tabs(["📋 要注意案件一覧", "🔍 案件ドリルダウン", "📝 AIレポート"])

RANK_COLOR = {"高": "#fde2e2", "中": "#fff4d6", "低": "#ffffff"}
RANK_ICON = {"高": "🔴 高", "中": "🟡 中", "低": "🟢 低"}

def display_table(o: pd.DataFrame) -> pd.DataFrame:
    t = pd.DataFrame({
        "重要度": o["rank"].map(RANK_ICON), "スコア": o["score"], "受注No": o["order_id"],
        "顧客": o["customer"], "品名": o["product"], "数量": o["qty"].astype("Int64"),
        "納期": o["due_date"].dt.strftime("%Y-%m-%d"), "納期まで": o["days_to_due"].astype(str) + "日",
        "進捗": o["progress"], "現在工程": o["current_process"], "該当ルール": o["issues"], "理由": o["reasons"]})
    return t

with tab1:
    c_a, c_b = st.columns([3, 2])
    show_all = c_a.toggle("問題のない案件も表示する", value=False)
    rank_filter = c_b.multiselect("重要度で絞り込み", ["高", "中", "低"], default=["高", "中", "低"])
    view = orders if show_all else flagged
    view = view[view["rank"].isin(rank_filter)]
    t = display_table(view)
    styled = (t.style.apply(lambda r: [f"background-color:{RANK_COLOR[view.loc[r.name, 'rank']]}"] * len(r), axis=1)
              .map(lambda v: "color:#b91c1c;font-weight:600" if isinstance(v, str) and v.startswith("🔴") else "",
                   subset=["重要度"]))
    st.dataframe(styled, width="stretch", hide_index=True, height=min(80 + 35 * len(t), 560),
                 column_config={"理由": st.column_config.TextColumn(width="large"),
                                "スコア": st.column_config.ProgressColumn(min_value=0, max_value=200, format="%d")})

    cols = st.columns(len(FLAG_ORDER))
    for c, f in zip(cols, FLAG_ORDER):
        c.metric(f, f"{int(orders[f'f_{f}'].sum())}件")

    out = view.copy()
    out["due_date"] = out["due_date"].dt.strftime("%Y-%m-%d")
    out = out.rename(columns={"order_id": "受注No", "customer": "顧客", "product": "品名", "qty": "数量",
                              "due_date": "納期", "days_to_due": "納期まで日数", "current_process": "現在工程",
                              "progress": "進捗", "remaining_days": "残工程予定日数", "score": "スコア",
                              "rank": "重要度", "issues": "該当ルール", "reasons": "理由", "hold_notes": "保留メモ"})
    out = out[[c for c in out.columns if not c.startswith("f_")]]
    st.download_button("⬇️ 抽出結果をCSVでダウンロード", out.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"要注意案件_{base_date:%Y%m%d}.csv", mime="text/csv")

with tab2:
    opts = orders["order_id"].tolist()
    labels = {r.order_id: f"{r.order_id}｜{r.customer}｜{r['product']}（{RANK_ICON[r['rank']]}）" for _, r in orders.iterrows()}
    sel = st.selectbox("案件を選択", opts, format_func=lambda x: labels[x])
    o = orders[orders["order_id"] == sel].iloc[0]
    g = procs[procs["order_id"] == sel].sort_values("process_seq")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("重要度", RANK_ICON[o["rank"]], f"スコア {o['score']}", delta_color="off")
    m2.metric("納期", f"{o['due_date']:%m/%d}", f"残り{o['days_to_due']}日", delta_color="off")
    m3.metric("進捗", o["progress"], o["current_process"], delta_color="off")
    m4.metric("残工程の予定日数", f"{o['remaining_days']}日")
    if o["issues"] != "問題なし":
        st.warning("**該当ルール：** " + o["issues"] + "\n\n" + "\n".join(f"- {x}" for x in o["reasons"].split(" ／ ")))
    else:
        st.success("問題は検知されていません。")

    # ガントチャート（予定 vs 実績）
    bars = []
    for _, r in g.iterrows():
        bars.append(dict(工程=f"{r.process_seq}. {r.process_name}", 区分="予定", 開始=r.planned_start,
                         終了=r.planned_end + timedelta(days=1), 状態=r.status))
        if pd.notna(r.actual_start):
            end = (r.actual_end if pd.notna(r.actual_end) else pd.Timestamp(base_date)) + timedelta(days=1)
            bars.append(dict(工程=f"{r.process_seq}. {r.process_name}", 区分="実績", 開始=r.actual_start,
                             終了=end, 状態=r.status))
    gdf = pd.DataFrame(bars)
    fig = px.timeline(gdf, x_start="開始", x_end="終了", y="工程", color="区分", pattern_shape="区分",
                      hover_data=["状態"], color_discrete_map={"予定": "#93c5fd", "実績": "#f97316"})
    fig.update_layout(barmode="group", height=120 + 50 * len(g), margin=dict(l=10, r=10, t=30, b=10),
                      legend=dict(orientation="h", y=1.08), xaxis_title=None, yaxis_title=None)
    fig.update_yaxes(autorange="reversed")
    for x, color, name in [(pd.Timestamp(base_date), "#dc2626", "基準日"), (o["due_date"], "#111827", "納期")]:
        fig.add_shape(type="line", x0=x, x1=x, y0=0, y1=1, yref="paper", line=dict(color=color, dash="dash"))
        fig.add_annotation(x=x, y=1, yref="paper", text=name, showarrow=False, yshift=10, font=dict(color=color))
    st.plotly_chart(fig, width="stretch")

    show = pd.DataFrame({
        "No": g["process_seq"], "工程": g["process_name"], "状態": g["status"],
        "予定開始": g["planned_start"].dt.strftime("%m/%d"), "予定完了": g["planned_end"].dt.strftime("%m/%d"),
        "実績開始": g["actual_start"].dt.strftime("%m/%d").fillna("-"),
        "実績完了": g["actual_end"].dt.strftime("%m/%d").fillna("-"),
        "最終更新": g["last_updated"].dt.strftime("%m/%d"),
        "検知": g["reasons"].apply(lambda x: " ／ ".join(x)), "メモ": g["note"]})
    st.dataframe(show.style.apply(lambda r: ["background-color:#fde2e2" if r["検知"] else ""] * len(r), axis=1),
                 width="stretch", hide_index=True)

with tab3:
    prov = llm_provider()
    if prov:
        st.info(f"AIプロバイダー：{prov[0]}（モデル：{prov[3]}）。ボタンを押すとAIがレポートを作成します。")
        if st.button("🤖 AIでレポートを生成", type="primary"):
            with st.spinner("AIがレポートを作成中…"):
                try:
                    st.session_state["ai_report"] = llm_report(build_llm_prompt(orders, base_date))
                except Exception as e:
                    st.session_state["ai_report"] = None
                    st.error(f"AI呼び出しに失敗したため、ルールベースのレポートを表示します（{e}）")
        if st.session_state.get("ai_report"):
            st.markdown('<span class="badge" style="background:#dbeafe;color:#1e40af">AI生成</span>', unsafe_allow_html=True)
            report = st.session_state["ai_report"]
        else:
            st.markdown('<span class="badge" style="background:#e5e7eb;color:#374151">ルールベース生成</span>', unsafe_allow_html=True)
            report = rule_based_report(orders, base_date, th)
    else:
        st.markdown('<span class="badge" style="background:#e5e7eb;color:#374151">ルールベース生成</span>'
                    '<span style="color:#6b7280;font-size:.85rem">（OPENAI_API_KEY / XAI_API_KEY を設定するとAIが文章を作成します）</span>',
                    unsafe_allow_html=True)
        report = rule_based_report(orders, base_date, th)
    with st.container(border=True):
        st.markdown(report)
    st.download_button("⬇️ レポートをテキストで保存", report.encode("utf-8"),
                       file_name=f"今週の対応レポート_{base_date:%Y%m%d}.md", mime="text/markdown")

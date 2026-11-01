"""納期遅れ・停滞の早期検知デモ。

サンプルCSVはアプリファイルと同じディレクトリから読む。
APIキーは環境変数または Streamlit Secrets のみ。リポジトリには置かない。
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

import logic

SAMPLE_PATH = Path(__file__).resolve().parent / "sample_orders.csv"
LEVEL_MARK = {"高": "🔴 高", "中": "🟡 中", "低": "🟢 低"}
LEVEL_COLOR = {"高": "#fde8e8", "中": "#fff6dc", "低": "#e7f6ea"}


def main() -> None:
    st.set_page_config(
        page_title="納期遅れ・停滞の早期検知デモ",
        page_icon="📊",
        layout="wide",
    )
    _inject_css()
    settings, uploaded = _sidebar()
    frame, source_label = _load_frame(uploaded)
    processes, orders = logic.analyze(
        frame,
        settings["today"],
        stall_days=settings["stall_days"],
        urgent_days=settings["urgent_days"],
        high_score=settings["high_score"],
        mid_score=settings["mid_score"],
    )
    _header()
    _kpis(orders, len(frame), settings)
    st.caption(f"データ：{source_label}　／　基準日：{settings['today'].isoformat()}")

    list_tab, detail_tab, report_tab = st.tabs(
        ["📋 要注意案件一覧", "🔍 案件ドリルダウン", "📝 AIレポート"]
    )
    with list_tab:
        _list_tab(orders, settings)
    with detail_tab:
        _detail_tab(orders, processes, settings["today"])
    with report_tab:
        _report_tab(orders, processes, settings, source_label, len(frame))


def _sidebar() -> tuple[dict, object]:
    st.sidebar.markdown("### ⚙️ 設定")
    uploaded = st.sidebar.file_uploader(
        "工程データCSVをアップロード",
        type=["csv"],
        key="orders_csv",
        help="1行が1受注の1工程です。UTF-8（BOM可）または Shift_JIS。"
        "未指定のときは架空のサンプルを使います。",
    )
    today = st.sidebar.date_input("基準日（今日）", value=logic.DEFAULT_TODAY)
    st.sidebar.markdown("**検知ルールのしきい値**")
    stall_days = int(
        st.sidebar.number_input(
            "停滞：最終更新からの日数",
            min_value=1,
            max_value=30,
            value=3,
            step=1,
            help="着手中のまま、この日数以上更新がない工程を停滞とします。",
        )
    )
    urgent_days = int(
        st.sidebar.number_input(
            "納期が近い：納期まで何日以内",
            min_value=0,
            max_value=60,
            value=7,
            step=1,
        )
    )
    with st.sidebar.expander("重要度スコアの境界"):
        high_score = int(st.number_input("「高」になるスコア", min_value=1, max_value=400, value=80, step=1))
        mid_score = int(st.number_input("「中」になるスコア", min_value=0, max_value=399, value=25, step=1))
        st.caption("加点：遅延、着手遅れ、停滞、納期リスク、保留。納期が近い問題案件はスコアに関わらず「高」です。")
    if mid_score >= high_score:
        mid_score = high_score - 1
    st.sidebar.caption("ルール：遅延 / 着手遅れ / 停滞 / 納期リスク / 保留")
    st.sidebar.caption(
        f"納期{urgent_days}日以内かつ何らかの問題がある案件は自動的に「高」になります。"
    )
    settings = {
        "today": _as_date(today),
        "stall_days": stall_days,
        "urgent_days": urgent_days,
        "high_score": high_score,
        "mid_score": mid_score,
    }
    return settings, uploaded


def _load_frame(uploaded) -> tuple[pd.DataFrame, str]:
    try:
        if uploaded is not None:
            frame = logic.read_orders(uploaded.getvalue())
            return frame, uploaded.name
        frame = logic.read_orders(SAMPLE_PATH)
    except ValueError as exc:
        st.error(str(exc))
        st.stop()
    except Exception as exc:
        st.error(f"CSVを読み込めませんでした: {exc}")
        st.stop()
    if frame.empty:
        st.warning("CSVに行がありません。")
        st.stop()
    return frame, "sample_orders.csv（架空のサンプルデータ）"


def _header() -> None:
    st.markdown("# 📊 納期遅れ・停滞の早期検知デモ")
    st.caption(
        "CSV読み込み → ルールで遅れ・停滞を抽出 → 重要度順に一覧 → 「今週手を打つべき案件」をレポート化"
    )


def _kpis(orders: pd.DataFrame, n_rows: int, settings: dict) -> None:
    attention = int(orders["any_flag"].sum())
    high = int((orders["importance"] == "高").sum())
    stalled = int(orders["n_stalled_processes"].sum())
    columns = st.columns(4)
    columns[0].metric("全案件数", len(orders))
    columns[0].caption(f"工程行数 {n_rows}")
    columns[1].metric("要注意案件数", attention)
    columns[1].caption("いずれかのルールに該当")
    columns[2].metric("高リスク件数", high)
    columns[2].caption(f"納期{settings['urgent_days']}日以内の問題案件など")
    columns[3].metric("停滞工程数", stalled)
    columns[3].caption(f"{settings['stall_days']}日以上更新なし（着手中）")


def _list_tab(orders: pd.DataFrame, settings: dict) -> None:
    left, right = st.columns([1.1, 1])
    show_clean = left.toggle("問題のない案件も表示する", value=False)
    levels = right.multiselect(
        "重要度で絞り込み",
        options=["高", "中", "低"],
        default=["高", "中", "低"],
    )
    view = orders
    if not show_clean:
        view = view[view["any_flag"]]
    if levels:
        view = view[view["importance"].isin(levels)]
    else:
        view = view.iloc[0:0]

    display = _display_frame(view)
    if display.empty:
        st.info("条件に合う案件がありません。")
    else:
        styler = display.style.apply(_paint_rows, axis=1)
        st.dataframe(
            styler,
            width="stretch",
            hide_index=True,
            height=min(560, 80 + 36 * len(display)),
            column_config={
                "スコア": st.column_config.ProgressColumn(
                    "スコア",
                    min_value=0,
                    max_value=200,
                    format="%d",
                ),
                "理由": st.column_config.TextColumn("理由", width="large"),
            },
        )
        csv_bytes = display.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "抽出結果をCSVでダウンロード",
            data=csv_bytes,
            file_name="attention_orders.csv",
            mime="text/csv",
        )

    st.markdown("##### ルール別件数")
    counts = st.columns(5)
    for column, (label, flag) in zip(
        counts,
        (
            ("遅延", "delayed"),
            ("着手遅れ", "start_delayed"),
            ("停滞", "stalled"),
            ("納期リスク", "due_risk"),
            ("保留", "on_hold"),
        ),
    ):
        column.metric(label, f"{int(view[flag].sum()) if len(view) else 0}件")
    st.caption(
        f"しきい値：停滞 {settings['stall_days']}日 ／ 納期が近い {settings['urgent_days']}日 ／ "
        f"高 {settings['high_score']}点 ／ 中 {settings['mid_score']}点"
    )


def _detail_tab(orders: pd.DataFrame, processes: pd.DataFrame, today: date) -> None:
    if orders.empty:
        st.info("案件がありません。")
        return
    labels = { _order_label(row): row.order_id for row in orders.itertuples(index=False) }
    selected_label = st.selectbox("案件を選択", list(labels))
    order_id = labels[selected_label]
    row = orders.loc[orders["order_id"] == order_id].iloc[0]
    group = processes[processes["order_id"] == order_id].sort_values("process_seq")

    metrics = st.columns(4)
    metrics[0].metric("重要度", row["importance"])
    metrics[0].caption(f"スコア {row['score']}")
    due_text = logic._md(row["due_date"]) if row["due_date"] is not None else "-"
    metrics[1].metric("納期", due_text)
    if row["days_until"] >= 0:
        metrics[1].caption(f"残り{row['days_until']}日")
    else:
        metrics[1].caption(f"{-row['days_until']}日超過")
    metrics[2].metric("進捗", row["progress"])
    metrics[2].caption(row["current_process"])
    metrics[3].metric("残工程の予定日数", f"{row['remaining_days']}日")

    reasons = list(row["reasons"])
    items = "".join(f"<li>{reason}</li>" for reason in reasons) or "<li>該当なし</li>"
    st.markdown(
        (
            f"<div class='rule-box'><b>該当ルール：{row['rules_label']}</b><ul>{items}</ul></div>"
        ),
        unsafe_allow_html=True,
    )
    st.plotly_chart(_gantt(group, today, logic._as_date(row["due_date"])), width="stretch")
    st.dataframe(_process_table(group), width="stretch", hide_index=True)


def _report_tab(
    orders: pd.DataFrame,
    processes: pd.DataFrame,
    settings: dict,
    source_label: str,
    n_rows: int,
) -> None:
    llm = _llm_settings()
    report = logic.rule_based_report(orders, settings["today"], settings["urgent_days"])
    source = "ルールベース生成"
    signature = (
        settings["today"].isoformat(),
        settings["stall_days"],
        settings["urgent_days"],
        settings["high_score"],
        settings["mid_score"],
        source_label,
        n_rows,
    )
    if llm is not None and st.button("AIでレポートを作成"):
        api_key, url, model = llm
        try:
            report = logic.llm_report(logic.build_llm_prompt(orders, settings["today"]), api_key, url, model)
            source = "AI生成"
            st.session_state["ai_report"] = report
            st.session_state["ai_signature"] = signature
        except Exception as exc:
            st.error(f"AIレポートの取得に失敗したため、ルールベースの文章を表示します。（{exc}）")
    elif llm is not None and st.session_state.get("ai_signature") == signature and st.session_state.get("ai_report"):
        report = st.session_state["ai_report"]
        source = "AI生成"

    hint = ""
    if llm is None:
        hint = "（`OPENAI_API_KEY` / `XAI_API_KEY` を設定するとAIが文章を作成します）"
    st.markdown(f"<span class='badge'>{source}</span> <span class='hint'>{hint}</span>", unsafe_allow_html=True)
    st.markdown(report)
    st.download_button(
        "レポートをMarkdownでダウンロード",
        data=report.encode("utf-8"),
        file_name="weekly_report.md",
        mime="text/markdown",
    )


def _llm_settings() -> tuple[str, str, str] | None:
    model_override = _secret("LLM_MODEL")
    xai_key = _secret("XAI_API_KEY")
    if xai_key:
        return xai_key, "https://api.x.ai/v1/chat/completions", model_override or "grok-4"
    openai_key = _secret("OPENAI_API_KEY")
    if openai_key:
        return openai_key, "https://api.openai.com/v1/chat/completions", model_override or "gpt-4o-mini"
    return None


def _secret(name: str) -> str | None:
    from_env = os.environ.get(name)
    if from_env:
        return from_env
    try:
        if name in st.secrets:
            value = st.secrets[name]
            return str(value) if value else None
    except Exception:
        return None
    return None


def _display_frame(view: pd.DataFrame) -> pd.DataFrame:
    if view.empty:
        return pd.DataFrame(
            columns=["重要度", "スコア", "受注No", "顧客", "品名", "数量", "納期", "納期まで", "進捗", "現在工程", "該当ルール", "理由"]
        )
    display = pd.DataFrame(
        {
            "重要度": view["importance"].map(LEVEL_MARK),
            "スコア": view["score"].astype(int),
            "受注No": view["order_id"],
            "顧客": view["customer"],
            "品名": view["product"],
            "数量": view["qty"].astype(int),
            "納期": view["due_date"].map(
                lambda value: parsed.isoformat() if (parsed := logic._as_date(value)) else ""
            ),
            "納期まで": view["days_until"].map(_days_label),
            "進捗": view["progress"],
            "現在工程": view["current_process"],
            "該当ルール": view["rules_label"],
            "理由": view["reasons"].map(lambda reasons: " / ".join(reasons)),
        }
    )
    return display.reset_index(drop=True)


def _paint_rows(row: pd.Series) -> list[str]:
    level = "低"
    for key, mark in LEVEL_MARK.items():
        if row["重要度"] == mark:
            level = key
    color = LEVEL_COLOR[level]
    return [f"background-color: {color}"] * len(row)


def _process_table(group: pd.DataFrame) -> pd.DataFrame:
    records = []
    for record in group.to_dict(orient="records"):
        detection = " / ".join(record["reason_parts"])
        records.append(
            {
                "No": int(record["process_seq"]) if pd.notna(record["process_seq"]) else "",
                "工程": record["process_name"],
                "状態": record["status"],
                "予定開始": _mmdd(record["planned_start"]),
                "予定完了": _mmdd(record["planned_end"]),
                "実績開始": _mmdd(record["actual_start"]),
                "実績完了": _mmdd(record["actual_end"]),
                "最終更新": _mmdd(record["last_updated"]),
                "検知": detection,
            }
        )
    table = pd.DataFrame(records)
    def paint(row: pd.Series) -> list[str]:
        if row["検知"]:
            return ["background-color: #fde8e8"] * len(row)
        return [""] * len(row)

    return table.style.apply(paint, axis=1)


def _gantt(group: pd.DataFrame, today: date, due: date | None):
    records = []
    for record in group.to_dict(orient="records"):
        label = f"{int(record['process_seq'])}. {record['process_name']}"
        planned_start = logic._as_date(record["planned_start"])
        planned_end = logic._as_date(record["planned_end"])
        if planned_start and planned_end:
            records.append(
                {
                    "工程": label,
                    "開始": _dt(planned_start),
                    "終了": _dt(planned_end + timedelta(days=1)),
                    "区分": "予定",
                }
            )
        actual_start = logic._as_date(record["actual_start"])
        actual_end = logic._as_date(record["actual_end"])
        if actual_start and actual_end is None and record["status"] in ("着手中", "保留"):
            actual_end = max(today, actual_start)
        if actual_start and actual_end:
            records.append(
                {
                    "工程": label,
                    "開始": _dt(actual_start),
                    "終了": _dt(actual_end + timedelta(days=1)),
                    "区分": "実績",
                }
            )
    chart = pd.DataFrame(records)
    figure = px.timeline(
        chart,
        x_start="開始",
        x_end="終了",
        y="工程",
        color="区分",
        color_discrete_map={"予定": "#4C78A8", "実績": "#F58518"},
    )
    for trace in figure.data:
        if trace.name == "実績":
            trace.marker.pattern = dict(shape="/")
    figure.add_vline(
        x=_dt(today).timestamp() * 1000,
        line_dash="dash",
        line_color="#d62728",
        annotation_text="基準日",
        annotation_position="top",
    )
    if due is not None:
        figure.add_vline(
            x=_dt(due).timestamp() * 1000,
            line_dash="dash",
            line_color="#222222",
            annotation_text="納期",
            annotation_position="top",
        )
    figure.update_layout(
        height=380,
        margin=dict(l=10, r=10, t=40, b=10),
        legend_title_text="区分",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        xaxis_title=None,
        yaxis_title=None,
        plot_bgcolor="white",
    )
    figure.update_yaxes(autorange="reversed")
    figure.update_xaxes(tickformat="%b %d", showgrid=True, gridcolor="#eeeeee")
    return figure


def _order_label(row) -> str:
    return f"{row.order_id}｜{row.customer}｜{row.product}（{LEVEL_MARK[row.importance]}）"


def _days_label(days: int) -> str:
    if days > 0:
        return f"{days}日"
    if days == 0:
        return "当日"
    return f"{-days}日超過"


def _mmdd(value) -> str:
    parsed = logic._as_date(value)
    if parsed is None:
        return "-"
    return f"{parsed.month:02d}/{parsed.day:02d}"


def _as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return logic.DEFAULT_TODAY


def _dt(value: date) -> datetime:
    return datetime(value.year, value.month, value.day)


def _inject_css() -> None:
    st.markdown(
        """
        <style>
        [data-testid="stMetricValue"] { font-variant-numeric: tabular-nums; }
        .rule-box {
            background: #fff8d6;
            border-radius: 8px;
            padding: 12px 16px;
            margin: 8px 0 16px 0;
        }
        .rule-box ul { margin: 8px 0 0 1.1rem; }
        .badge {
            display: inline-block;
            background: #eef2ff;
            color: #24315e;
            border-radius: 999px;
            padding: 2px 10px;
            font-weight: 600;
            margin-right: 8px;
        }
        .hint { color: #666; font-size: 0.9rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
else:
    # Streamlit はスクリプトとして実行する。AppTest も __name__ が "__main__" になる。
    # 直接 import されたときだけ画面を出さない。
    pass

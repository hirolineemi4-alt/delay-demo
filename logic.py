"""納期遅れ・停滞の検知、スコアリング、レポート生成。

Streamlit に依存しない。基準日・しきい値は呼び出し側から渡す。
"""

from __future__ import annotations

import io
import json
import os
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests

REQUIRED_COLUMNS = [
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

DATE_COLUMNS = [
    "due_date",
    "planned_start",
    "planned_end",
    "actual_start",
    "actual_end",
    "last_updated",
]

HOLD_KEYWORDS = ("待ち", "不足", "確認中")
DEFAULT_TODAY = date(2026, 11, 10)
LEVEL_RANK = {"高": 0, "中": 1, "低": 2}

SYSTEM_PROMPT = (
    "あなたは製造業の生産管理コンサルタントです。"
    "簡潔で実務的な日本語で答えてください。"
    "データにない事実は創作しないでください。"
)


def read_orders(source) -> pd.DataFrame:
    """CSV（パス、bytes、ファイルオブジェクト）を読み、日付を変換する。

    UTF-8（BOM 可）で失敗したら Shift_JIS（cp932）を試す。
    """
    raw = _read_bytes(source)
    last_error: Exception | None = None
    text = None
    for encoding in ("utf-8-sig", "cp932"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError as exc:
            last_error = exc
    if text is None:
        raise ValueError(f"CSVの文字コードを判定できませんでした: {last_error}")

    frame = pd.read_csv(io.StringIO(text))
    frame.columns = [str(col).strip() for col in frame.columns]
    missing = [col for col in REQUIRED_COLUMNS if col not in frame.columns]
    if missing:
        raise ValueError("必須列がありません: " + ", ".join(missing))

    frame = frame[REQUIRED_COLUMNS].copy()
    frame["order_id"] = frame["order_id"].astype(str).str.strip()
    frame["customer"] = frame["customer"].fillna("").astype(str).str.strip()
    frame["product"] = frame["product"].fillna("").astype(str).str.strip()
    frame["process_name"] = frame["process_name"].fillna("").astype(str).str.strip()
    frame["status"] = frame["status"].fillna("").astype(str).str.strip()
    frame["note"] = frame["note"].fillna("").astype(str).str.strip()
    frame["qty"] = pd.to_numeric(frame["qty"], errors="coerce").fillna(0).astype(int)
    frame["process_seq"] = pd.to_numeric(frame["process_seq"], errors="coerce")
    for column in DATE_COLUMNS:
        frame[column] = pd.to_datetime(frame[column], errors="coerce")
    frame = frame.sort_values(["order_id", "process_seq"], kind="mergesort").reset_index(drop=True)
    return frame


def detect_processes(frame: pd.DataFrame, today: date, stall_days: int) -> pd.DataFrame:
    """工程行に遅延・着手遅れ・停滞・保留のフラグと理由を付ける。"""
    rows: list[dict] = []
    for record in frame.to_dict(orient="records"):
        status = record["status"]
        incomplete = status != "完了"
        planned_start = _as_date(record["planned_start"])
        planned_end = _as_date(record["planned_end"])
        last_updated = _as_date(record["last_updated"])
        note = record["note"] or ""

        delay_days = 0
        is_delayed = False
        if incomplete and planned_end is not None and today > planned_end:
            is_delayed = True
            delay_days = (today - planned_end).days

        start_delay_days = 0
        is_start_delayed = False
        if status == "未着手" and planned_start is not None and today > planned_start:
            is_start_delayed = True
            start_delay_days = (today - planned_start).days

        stale_days = 0
        is_stalled = False
        if status == "着手中" and last_updated is not None:
            stale_days = (today - last_updated).days
            is_stalled = stale_days >= stall_days

        is_on_hold = status == "保留" or (incomplete and _contains_any(note, HOLD_KEYWORDS))
        duration_days = 0
        if planned_start is not None and planned_end is not None:
            duration_days = max(0, (planned_end - planned_start).days + 1)

        parts: list[str] = []
        name = record["process_name"]
        if is_delayed and planned_end is not None:
            parts.append(f"遅延：{name}（予定完了{_md(planned_end)}、{delay_days}日超過）")
        if is_stalled:
            parts.append(f"停滞：{name}（{stale_days}日間更新なし）")
        if is_start_delayed and planned_start is not None:
            parts.append(f"着手遅れ：{name}（予定開始{_md(planned_start)}、未着手）")
        if is_on_hold:
            parts.append(f"保留：{name}（{note}）" if note else f"保留：{name}")

        enriched = dict(record)
        enriched.update(
            {
                "is_incomplete": incomplete,
                "is_delayed": is_delayed,
                "delay_days": delay_days,
                "is_start_delayed": is_start_delayed,
                "start_delay_days": start_delay_days,
                "is_stalled": is_stalled,
                "stale_days": stale_days,
                "is_on_hold": is_on_hold,
                "duration_days": duration_days,
                "reason_parts": parts,
            }
        )
        rows.append(enriched)
    return pd.DataFrame(rows)


def summarize_orders(
    processes: pd.DataFrame,
    today: date,
    stall_days: int,
    urgent_days: int,
    high_score: int,
    mid_score: int,
) -> pd.DataFrame:
    """工程フラグを受注単位に集約し、納期リスク・スコア・重要度を付ける。"""
    summaries: list[dict] = []
    if processes.empty:
        return pd.DataFrame(summaries)

    ordered = processes.sort_values(["order_id", "process_seq"], kind="mergesort")
    for order_id, group in ordered.groupby("order_id", sort=False):
        first = group.iloc[0]
        due = _as_date(first["due_date"])
        incomplete = group[group["is_incomplete"]]
        remaining = int(incomplete["duration_days"].sum()) if len(incomplete) else 0
        if due is None:
            days_until = 10**6
            due_risk = False
            shortage = 0
        else:
            days_until = (due - today).days
            due_risk = remaining > days_until
            shortage = remaining - days_until if due_risk else 0

        delayed = bool(group["is_delayed"].any())
        start_delayed = bool(group["is_start_delayed"].any())
        stalled = bool(group["is_stalled"].any())
        on_hold = bool(group["is_on_hold"].any())
        any_flag = delayed or start_delayed or stalled or due_risk or on_hold

        max_delay = int(group.loc[group["is_delayed"], "delay_days"].max()) if delayed else 0
        max_start = (
            int(group.loc[group["is_start_delayed"], "start_delay_days"].max())
            if start_delayed
            else 0
        )
        max_stale = int(group.loc[group["is_stalled"], "stale_days"].max()) if stalled else 0

        score = 0
        if delayed:
            score += 30 + min(30, 3 * max_delay)
        if start_delayed:
            score += 15 + min(15, 2 * max_start)
        if stalled:
            score += 25 + min(20, 3 * (max_stale - stall_days))
        if due_risk:
            score += 25 + min(30, 3 * shortage)
        if on_hold:
            score += 25

        urgent = bool(any_flag and 0 <= days_until <= urgent_days)
        past_due = bool(any_flag and days_until < 0)
        if urgent:
            score += 40
        if past_due:
            score += 30

        if urgent or score >= high_score:
            importance = "高"
        elif score >= mid_score:
            importance = "中"
        else:
            importance = "低"

        labels = []
        if delayed:
            labels.append("遅延")
        if start_delayed:
            labels.append("着手遅れ")
        if stalled:
            labels.append("停滞")
        if due_risk:
            labels.append("納期リスク")
        if on_hold:
            labels.append("保留")

        reasons: list[str] = []
        for parts in group["reason_parts"]:
            reasons.extend(parts)
        if due_risk:
            if days_until >= 0:
                reasons.append(
                    f"納期リスク：残工程の予定日数{remaining}日 > 納期まで{days_until}日（{shortage}日不足）"
                )
            else:
                reasons.append(
                    f"納期リスク：残工程の予定日数{remaining}日 > 納期を{-days_until}日超過（{shortage}日不足）"
                )

        if len(incomplete):
            current = incomplete.iloc[0]
            current_process = f"{current['process_name']}（{current['status']}）"
            open_notes = " ".join(note for note in incomplete["note"].tolist() if note)
        else:
            current_process = "完了"
            open_notes = ""

        n_total = int(len(group))
        n_done = int((~group["is_incomplete"]).sum())
        summaries.append(
            {
                "order_id": order_id,
                "customer": first["customer"],
                "product": first["product"],
                "qty": int(first["qty"]),
                "due_date": due,
                "days_until": int(days_until),
                "n_total": n_total,
                "n_done": n_done,
                "progress": f"{n_done}/{n_total}",
                "current_process": current_process,
                "remaining_days": remaining,
                "shortage_days": int(shortage),
                "delayed": delayed,
                "start_delayed": start_delayed,
                "stalled": stalled,
                "due_risk": due_risk,
                "on_hold": on_hold,
                "any_flag": any_flag,
                "n_stalled_processes": int(group["is_stalled"].sum()),
                "score": int(score),
                "importance": importance,
                "urgent": urgent,
                "past_due": past_due,
                "rules_label": "・".join(labels) if labels else "問題なし",
                "reasons": reasons,
                "open_notes": open_notes,
            }
        )

    result = pd.DataFrame(summaries)
    result["_rank"] = result["importance"].map(LEVEL_RANK)
    result = result.sort_values(
        ["_rank", "score", "days_until", "order_id"],
        ascending=[True, False, True, True],
        kind="mergesort",
    ).drop(columns="_rank")
    return result.reset_index(drop=True)


def analyze(
    frame: pd.DataFrame,
    today: date,
    stall_days: int = 3,
    urgent_days: int = 7,
    high_score: int = 80,
    mid_score: int = 25,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    processes = detect_processes(frame, today, stall_days)
    orders = summarize_orders(processes, today, stall_days, urgent_days, high_score, mid_score)
    return processes, orders


def rule_based_report(
    orders: pd.DataFrame,
    today: date,
    urgent_days: int,
    n_process_rows: int | None = None,
) -> str:
    """テンプレートで「今週手を打つべき案件」レポートを作る。"""
    if orders.empty:
        return f"# 今週手を打つべき案件レポート（基準日：{_jp_date(today)}）\n\n案件がありません。"

    attention = orders[orders["any_flag"]]
    n_orders = len(orders)
    n_attention = int(len(attention))
    n_high = int((orders["importance"] == "高").sum())
    counts = {
        "遅延": int(orders["delayed"].sum()),
        "着手遅れ": int(orders["start_delayed"].sum()),
        "停滞": int(orders["stalled"].sum()),
        "納期リスク": int(orders["due_risk"].sum()),
        "保留": int(orders["on_hold"].sum()),
    }
    notes = " ".join(attention["open_notes"].tolist())
    external = _contains_any(notes, ("外注", "材料", "図面", "待ち", "確認中"))

    lines = [
        f"# 今週手を打つべき案件レポート（基準日：{_jp_date(today)}）",
        "",
        "## 全体所見",
        f"- 全{n_orders}案件のうち、要注意案件は{n_attention}件、うち高リスクは{n_high}件です。",
        (
            "- 内訳：遅延{遅延}件、着手遅れ{着手遅れ}件、停滞{停滞}件、"
            "納期リスク{納期リスク}件、保留{保留}件（重複あり）"
        ).format(**counts),
    ]
    if n_process_rows is not None:
        lines.append(f"- 工程行数は{n_process_rows}行です。")
    if external:
        lines.append(
            "- 材料・外注・図面確認など社外要因による停止が含まれます。"
            "社内努力だけでは解消しないため、先方への確認を最優先にしてください。"
        )
    elif n_attention:
        lines.append("- 社外要因による停止は見当たりません。社内工程の遅れと停滞を優先して確認してください。")
    else:
        lines.append("- この基準日では、検知ルールに該当する案件はありません。")

    lines.extend(["", "## 今週対応すべき案件トップ5"])
    top = attention.head(5)
    if top.empty:
        lines.append("- 今週すぐに手を打つべき案件はありません。")
    for index, row in enumerate(top.itertuples(index=False), start=1):
        due_value = _as_date(row.due_date)
        due = _md(due_value) if due_value is not None else "未設定"
        if row.days_until >= 0:
            remain = f"残{row.days_until}日"
        else:
            remain = f"{-row.days_until}日超過"
        lines.append(
            f"{index}. {row.order_id} | {row.customer} | {row.product} {row.qty}個"
            f" — 重要度 {row.importance}（スコア{row.score}）"
        )
        lines.append(f"   - 納期：{due}（{remain}）　現在工程：{row.current_process}")
        lines.append("   - 理由：")
        for reason in row.reasons:
            lines.append(f"     - {reason}")
        actions = recommended_actions(row, urgent_days)
        lines.append(f"   - 推奨アクション：{'、'.join(actions)}")

    lines.extend(
        [
            "",
            "## 今週の進め方",
            "1. 重要度「高」の案件から、担当と本日の打ち手を決める。",
            "2. 外注・材料・図面など社外待ちは、先方へ本日中に状況を確認する。",
            "3. 停滞している工程は、止まっている理由を確認し進捗を更新する。",
            "4. 納期が近い案件は、遅れそうな時点で顧客へ事前連絡する。",
        ]
    )
    return "\n".join(lines) + "\n"


def recommended_actions(row, urgent_days: int) -> list[str]:
    notes = row.open_notes or ""
    actions: list[str] = []
    if "外注" in notes:
        actions.append("外注先に戻り日を確認する")
    if "材料" in notes:
        actions.append("材料の入荷日を確認し、着手日を組み直す")
    if "図面" in notes or "確認中" in notes:
        actions.append("図面確認の回答を先方に督促する")
    if "人員" in notes or ("不足" in notes and "材料" not in notes):
        actions.append("人員を手配するか、工程を振り替える")
    if row.stalled:
        actions.append("停滞工程の現状を確認し、最終更新を実態に合わせる")
    if row.delayed or row.start_delayed:
        actions.append("遅れている工程を前倒しできるか検討する")
    if row.due_risk:
        actions.append("残工程の予定を見直し、納期に収まるよう組み替える")
    if 0 <= row.days_until <= urgent_days:
        actions.append("顧客へ納期遅延の可能性を事前連絡する")
    if not actions:
        actions.append("進捗を確認し、必要なら計画を更新する")
    return actions


def build_llm_prompt(orders: pd.DataFrame, today: date) -> str:
    attention = orders[orders["any_flag"]].head(8)
    payload = []
    for row in attention.itertuples(index=False):
        payload.append(
            {
                "order_id": row.order_id,
                "customer": row.customer,
                "product": row.product,
                "qty": int(row.qty),
                "due_date": _as_date(row.due_date).isoformat() if _as_date(row.due_date) else None,
                "days_until": int(row.days_until),
                "importance": row.importance,
                "score": int(row.score),
                "current_process": row.current_process,
                "flags": row.rules_label,
                "reasons": list(row.reasons),
            }
        )
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    return (
        f"基準日: {today.isoformat()}\n"
        f"全案件数: {len(orders)}\n"
        f"要注意案件（上位{len(payload)}件）:\n{body}\n\n"
        "次の構成でレポートを書いてください。\n"
        "①全体所見（3行以内）\n"
        "②今週対応すべきトップ5（各案件の理由と推奨アクション。"
        "外注先確認、工程の前倒し、顧客への事前連絡など、データから言える範囲で）\n"
        "③今週の進め方\n"
        "データにない事実は創作しないでください。\n"
    )


def llm_report(user_prompt: str, api_key: str, url: str, model: str) -> str:
    """xAI または OpenAI 互換の chat completions を1回呼ぶ。"""
    response = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "temperature": 0.3,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        },
        timeout=90,
    )
    response.raise_for_status()
    payload = response.json()
    return payload["choices"][0]["message"]["content"]


def resolve_llm_from_env() -> tuple[str, str, str] | None:
    """環境変数だけを見る。キーが無ければ None。秘密はファイルに書かない。"""
    model_override = os.environ.get("LLM_MODEL") or None
    xai_key = os.environ.get("XAI_API_KEY")
    if xai_key:
        return xai_key, "https://api.x.ai/v1/chat/completions", model_override or "grok-4"
    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        return openai_key, "https://api.openai.com/v1/chat/completions", model_override or "gpt-4o-mini"
    return None


def sample_csv_path() -> Path:
    return Path(__file__).resolve().parent / "sample_orders.csv"


def _read_bytes(source) -> bytes:
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    if isinstance(source, Path):
        return source.read_bytes()
    if isinstance(source, str):
        return Path(source).read_bytes()
    if hasattr(source, "getvalue"):
        data = source.getvalue()
        return data if isinstance(data, bytes) else str(data).encode("utf-8")
    if hasattr(source, "read"):
        data = source.read()
        if isinstance(data, str):
            return data.encode("utf-8")
        return data
    raise TypeError(f"読み込めない入力です: {type(source)!r}")


def _as_date(value) -> date | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        return value.date()
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def _md(value: date | None) -> str:
    if value is None:
        return "-"
    return f"{value.month}/{value.day}"


def _jp_date(value: date) -> str:
    return f"{value.year}年{value.month}月{value.day}日"


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


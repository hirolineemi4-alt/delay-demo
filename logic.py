"""納期遅れ・停滞の検知ロジック（Streamlitに依存しない純粋関数）。"""
from __future__ import annotations
import io
import json
import os
from dataclasses import dataclass
import pandas as pd

REQUIRED_COLUMNS = ["order_id", "customer", "product", "qty", "due_date", "process_seq", "process_name",
                    "planned_start", "planned_end", "actual_start", "actual_end", "status", "last_updated", "note"]
DATE_COLUMNS = ["due_date", "planned_start", "planned_end", "actual_start", "actual_end", "last_updated"]
HOLD_KEYWORDS = ["待ち", "不足", "確認中"]
FLAG_ORDER = ["遅延", "着手遅れ", "停滞", "納期リスク", "保留"]
# 遅れが確定する前に朝見ておく前兆。APIキーなしのルールで判定する。
SLIP_DAYS = 2  # 計画と実績のズレは「2日を超える」
PRECURSOR_ORDER = [
    "未着手の超過",
    "状態がN日変わらない停滞",
    "計画と実績のズレ2日超",
    "完了見込みが納期超過",
    "材料入荷が着手予定より後",
]
PROCESS_PRECURSORS = [
    "未着手の超過",
    "状態がN日変わらない停滞",
    "計画と実績のズレ2日超",
    "材料入荷が着手予定より後",
]
OPTIONAL_COLUMNS = ["material_arrival"]


@dataclass
class Thresholds:
    stall_days: int = 3          # 停滞とみなす未更新日数
    due_window: int = 7          # 「納期が近い」とみなす日数
    high_score: int = 80         # これ以上は「高」
    mid_score: int = 25          # これ以上は「中」


def read_csv(src) -> pd.DataFrame:
    """CSVを読み込む（UTF-8 / UTF-8 BOM / Shift_JIS(cp932) に対応）。"""
    raw = src.read() if hasattr(src, "read") else open(src, "rb").read()
    for enc in ("utf-8-sig", "cp932"):
        try:
            df = pd.read_csv(io.BytesIO(raw), encoding=enc, dtype=str, keep_default_na=False)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("文字コードを判別できませんでした（UTF-8 または Shift_JIS で保存してください）")
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"必須列が不足しています: {', '.join(missing)}")
    for c in OPTIONAL_COLUMNS:
        if c not in df.columns:
            df[c] = ""
    df = df[REQUIRED_COLUMNS + OPTIONAL_COLUMNS].copy()
    for c in DATE_COLUMNS + OPTIONAL_COLUMNS:
        df[c] = pd.to_datetime(df[c].astype(str).str.strip().replace({"": None, "nan": None, "NaT": None}),
                               errors="coerce")
    df["qty"] = pd.to_numeric(df["qty"], errors="coerce")
    df["process_seq"] = pd.to_numeric(df["process_seq"], errors="coerce")
    for c in ["status", "note", "process_name", "order_id", "customer", "product"]:
        df[c] = df[c].fillna("").astype(str).str.strip()
    return df.sort_values(["order_id", "process_seq"]).reset_index(drop=True)


def _fmt(dt) -> str:
    return "-" if pd.isna(dt) else f"{pd.Timestamp(dt).month}/{pd.Timestamp(dt).day}"


def plan_actual_slip_days(p: pd.DataFrame, today) -> pd.Series:
    """計画と実績のズレ（日）。早まりは0。未完了で予定完了を過ぎている分も含む。"""
    today = pd.Timestamp(today).normalize()
    start_slip = (p["actual_start"] - p["planned_start"]).dt.days
    end_slip = (p["actual_end"] - p["planned_end"]).dt.days
    overrun = (today - p["planned_end"]).dt.days
    still_open = (p["status"] != "完了") & p["actual_start"].notna() & p["planned_end"].notna() & (today > p["planned_end"])
    open_overrun = overrun.where(still_open)
    slip = pd.concat([start_slip, end_slip, open_overrun], axis=1).max(axis=1, skipna=True)
    return slip.fillna(0).clip(lower=0).astype(int)


def detect_processes(df: pd.DataFrame, today, th: Thresholds) -> pd.DataFrame:
    """工程単位でフラグを付与する。"""
    today = pd.Timestamp(today).normalize()
    p = df.copy()
    not_done = p["status"] != "完了"
    p["days_late"] = (today - p["planned_end"]).dt.days.where(not_done)
    p["days_since_update"] = (today - p["last_updated"]).dt.days
    p["f_遅延"] = not_done & (today > p["planned_end"])
    p["f_着手遅れ"] = (p["status"] == "未着手") & (today > p["planned_start"])
    p["f_停滞"] = (p["status"] == "着手中") & (p["days_since_update"] >= th.stall_days)
    note_hit = p["note"].apply(lambda s: any(k in s for k in HOLD_KEYWORDS))
    p["f_保留"] = (p["status"] == "保留") | (not_done & note_hit)
    p["planned_days"] = (p["planned_end"] - p["planned_start"]).dt.days + 1
    if "material_arrival" not in p.columns:
        p["material_arrival"] = pd.NaT
    p["slip_days"] = plan_actual_slip_days(p, today)
    # 前兆は既存の5ルールとは別に持つ。未着手の「状態が動かない」は着手予定前なら正常なので、
    # 停滞の前兆は着手中・保留だけを見る（着手予定を過ぎた未着手は「未着手の超過」）。
    p["f_未着手の超過"] = (p["status"] == "未着手") & p["planned_start"].notna() & (today > p["planned_start"])
    p["f_状態がN日変わらない停滞"] = p["status"].isin(["着手中", "保留"]) & (p["days_since_update"] >= th.stall_days)
    p["f_計画と実績のズレ2日超"] = p["slip_days"] > SLIP_DAYS
    p["f_材料入荷が着手予定より後"] = (
        p["material_arrival"].notna() & p["planned_start"].notna()
        & (p["material_arrival"] > p["planned_start"]) & (p["status"] != "完了")
    )

    reasons = []
    precursor_reasons = []
    for _, r in p.iterrows():
        rs = []
        if r["f_遅延"]:
            rs.append(f"遅延：{r.process_name}（予定完了{_fmt(r.planned_end)}、{int(r.days_late)}日超過）")
        if r["f_着手遅れ"]:
            rs.append(f"着手遅れ：{r.process_name}（予定開始{_fmt(r.planned_start)}、未着手）")
        if r["f_停滞"]:
            rs.append(f"停滞：{r.process_name}（{int(r.days_since_update)}日間更新なし）")
        if r["f_保留"]:
            rs.append(f"保留：{r.process_name}（{r.note or '状態=保留'}）")
        reasons.append(rs)
        prs = []
        if r["f_未着手の超過"]:
            prs.append(f"未着手の超過：{r.process_name}（予定開始{_fmt(r.planned_start)}を過ぎても未着手）")
        if r["f_状態がN日変わらない停滞"]:
            prs.append(
                f"状態がN日変わらない停滞：{r.process_name}"
                f"（{r.status}のまま{int(r.days_since_update)}日更新なし、しきい値{th.stall_days}日）"
            )
        if r["f_計画と実績のズレ2日超"]:
            prs.append(f"計画と実績のズレ2日超：{r.process_name}（ずれ{int(r.slip_days)}日）")
        if r["f_材料入荷が着手予定より後"]:
            prs.append(
                f"材料入荷が着手予定より後：{r.process_name}"
                f"（入荷{_fmt(r.material_arrival)} ＞ 着手予定{_fmt(r.planned_start)}）"
            )
        precursor_reasons.append(prs)
    p["reasons"] = reasons
    p["precursor_reasons"] = precursor_reasons
    return p


def summarize_orders(p: pd.DataFrame, today, th: Thresholds) -> pd.DataFrame:
    """受注単位で集約し、スコアと重要度（高/中/低）を算出する。"""
    today = pd.Timestamp(today).normalize()
    out = []
    for oid, g in p.groupby("order_id", sort=False):
        g = g.sort_values("process_seq")
        first = g.iloc[0]
        due = first["due_date"]
        days_to_due = int((due - today).days) if pd.notna(due) else None
        all_done = (g["status"] == "完了").all()
        remaining = g.loc[g["status"] != "完了", "planned_days"].sum()
        risk = (not all_done) and days_to_due is not None and remaining > days_to_due
        shortage = int(remaining - days_to_due) if risk else 0

        flags = {f: bool(g[f"f_{f}"].any()) for f in ["遅延", "着手遅れ", "停滞", "保留"]}
        flags["納期リスク"] = bool(risk)
        reasons = [x for lst in g["reasons"] for x in lst]
        if risk:
            reasons.append(f"納期リスク：残工程の予定日数{int(remaining)}日 ＞ 納期まで{days_to_due}日（{shortage}日不足）")

        pre_flags = {f: bool(g[f"f_{f}"].any()) for f in PROCESS_PRECURSORS}
        pre_reasons = [x for lst in g["precursor_reasons"] for x in lst]
        # 完了見込み = 基準日 + 残工程の予定日数 + まだ着いていない材料を待つ日数。
        # 残工程の数え方は既存の納期リスクと同じ（未完了工程の予定日数の合計）。
        material_wait = 0
        if not all_done:
            for _, row in g[g["status"] != "完了"].iterrows():
                mat = row["material_arrival"]
                if pd.notna(mat) and mat > today and row["status"] != "着手中":
                    material_wait = max(material_wait, int((mat - today).days))
        if all_done or pd.isna(due) or days_to_due is None:
            forecast = g["actual_end"].max() if all_done else pd.NaT
            impact = 0
            pre_flags["完了見込みが納期超過"] = False
        else:
            forecast = today + pd.Timedelta(days=int(remaining) + material_wait)
            impact = max(0, int((forecast - due).days))
            pre_flags["完了見込みが納期超過"] = bool(forecast > due)
            if pre_flags["完了見込みが納期超過"]:
                wait_txt = f"、うち材料待ち{material_wait}日" if material_wait else ""
                pre_reasons.append(
                    f"完了見込みが納期超過：完了見込み{_fmt(forecast)} ＞ 納期{_fmt(due)}"
                    f"（納期への影響{impact}日{wait_txt}）"
                )

        # --- スコア ---
        score = 0
        if flags["遅延"]:
            score += 30 + min(30, 3 * int(g["days_late"].where(g["f_遅延"]).max()))
        if flags["着手遅れ"]:
            late_start = (today - g.loc[g["f_着手遅れ"], "planned_start"]).dt.days.max()
            score += 15 + min(15, 2 * int(late_start))
        if flags["停滞"]:
            stale = int(g.loc[g["f_停滞"], "days_since_update"].max())
            score += 25 + min(20, 3 * (stale - th.stall_days))
        if flags["納期リスク"]:
            score += 25 + min(30, 3 * shortage)
        if flags["保留"]:
            score += 25
        any_flag = any(flags.values())
        urgent = any_flag and days_to_due is not None and days_to_due <= th.due_window
        if urgent:
            score += 40
        if any_flag and days_to_due is not None and days_to_due < 0:
            score += 30  # 納期超過

        if urgent or score >= th.high_score:
            rank = "高"
        elif score >= th.mid_score:
            rank = "中"
        else:
            rank = "低"

        cur = g[g["status"] != "完了"]
        current = "全工程完了" if cur.empty else f"{cur.iloc[0].process_name}（{cur.iloc[0].status}）"
        notes = "／".join(n for n in g["note"] if n and any(k in n for k in HOLD_KEYWORDS))
        out.append(dict(
            order_id=oid, customer=first["customer"], product=first["product"], qty=first["qty"],
            due_date=due, days_to_due=days_to_due, current_process=current,
            progress=f"{int((g['status'] == '完了').sum())}/{len(g)}",
            remaining_days=int(remaining), score=int(score), rank=rank,
            issues="・".join(f for f in FLAG_ORDER if flags[f]) or "問題なし",
            reasons=" ／ ".join(reasons), hold_notes=notes,
            impact_days=int(impact), forecast_date=forecast, material_wait=int(material_wait),
            precursors="・".join(f for f in PRECURSOR_ORDER if pre_flags[f]) or "なし",
            precursor_reasons=" ／ ".join(pre_reasons),
            **{f"f_{k}": v for k, v in {**flags, **pre_flags}.items()},
        ))
    o = pd.DataFrame(out)
    rank_order = {"高": 0, "中": 1, "低": 2}
    o["_r"] = o["rank"].map(rank_order)
    o = o.sort_values(["_r", "score", "days_to_due"], ascending=[True, False, True]).drop(columns="_r")
    return o.reset_index(drop=True)


def analyze(df: pd.DataFrame, today, th: Thresholds):
    p = detect_processes(df, today, th)
    o = summarize_orders(p, today, th)
    return p, o


def morning_three(o: pd.DataFrame, n: int = 3) -> pd.DataFrame:
    """重要度順を保ったまま、前兆がある案件から今朝見る上位n件を返す。"""
    if o.empty or "precursors" not in o.columns:
        return o.head(0)
    return o[o["precursors"] != "なし"].head(n).reset_index(drop=True)


# ---------------- レポート ----------------
def recommend_actions(row: pd.Series, th: Thresholds) -> list[str]:
    acts = []
    notes = row.get("hold_notes", "") or ""
    if "外注" in notes:
        acts.append("外注先へ戻り日を電話で確認し、確定日を回答してもらう")
    if "材料" in notes:
        acts.append("仕入先へ入荷日を再確認。間に合わない場合は代替材・在庫材の流用を検討")
    if "図面" in notes or "確認中" in notes:
        acts.append("顧客へ回答期限を区切って図面確認を依頼（回答待ちで工程が止まっている旨を共有）")
    if "人員" in notes or "不足" in notes:
        acts.append("他班からの応援・残業／休日出勤で人員を補填")
    if row["f_停滞"] or row.get("f_状態がN日変わらない停滞"):
        acts.append("担当者に現状をヒアリングし、止まっている原因（設備・治具・段取り）を当日中に特定")
    if row["f_遅延"] or row["f_着手遅れ"] or row.get("f_未着手の超過"):
        acts.append("後工程の段取りを前倒しし、設備の空き枠に割り込みを検討")
    if row.get("f_計画と実績のズレ2日超"):
        acts.append("計画を実績に合わせて引き直し、後工程の開始日を見直す")
    if row.get("f_材料入荷が着手予定より後") and "材料" not in notes:
        acts.append("材料の入荷が着手予定に間に合うか仕入先へ確認し、間に合わなければ代替材を検討")
    if row["f_納期リスク"] or row.get("f_完了見込みが納期超過"):
        acts.append("工程の並行化・外注活用で短縮できるか検討")
        if row["days_to_due"] is not None and row["days_to_due"] <= th.due_window:
            acts.append("顧客へ事前連絡し、分納や納期調整の可能性を早めに相談")
    if not acts:
        acts.append("進捗を日次で確認")
    return list(dict.fromkeys(acts))


def rule_based_report(o: pd.DataFrame, today, th: Thresholds, top_n: int = 5) -> str:
    today = pd.Timestamp(today)
    flagged = o[o["issues"] != "問題なし"]
    top = flagged.head(top_n)
    n_high = int((o["rank"] == "高").sum())
    lines = [f"## 今週手を打つべき案件レポート（基準日：{today:%Y年%m月%d日}）", "",
             "いまの工程表（スプレッドシート）の代わりではなく、遅れの前兆を見えるように足したものです。",
             "候補はルールからの提案です。手を打つ前に、人が内容を確認してください。", ""]
    top3 = morning_three(o, 3)
    lines += ["### 今朝見る3件（提案 → 人が確認）", ""]
    if top3.empty:
        lines.append("- 前兆ルールに該当する案件はありません。")
        lines.append("")
    else:
        lines.append("- 画面上の「確認した」は、人がこの提案を見た、という印です。")
        for i, (_, r) in enumerate(top3.iterrows(), 1):
            lines.append(
                f"**{i}. {r.order_id}｜{r.customer}｜{r['product']}**"
                f" — 納期への影響 **+{int(r.impact_days)}日**"
            )
            lines.append(f"- 完了見込み：{_fmt(r.forecast_date)}　納期：{_fmt(r.due_date)}　前兆：{r.precursors}")
            for rs in str(r.precursor_reasons).split(" ／ "):
                if rs and rs != "nan":
                    lines.append(f"  - {rs}")
        lines.append("")
    lines += ["### 全体所見",
             f"- 全{len(o)}案件のうち、要注意案件は **{len(flagged)}件**、うち高リスクは **{n_high}件** です。"]
    cnt = {f: int(o[f"f_{f}"].sum()) for f in FLAG_ORDER}
    lines.append("- 内訳：" + "、".join(f"{k} {v}件" for k, v in cnt.items()) + "（重複あり）")
    if cnt["保留"]:
        lines.append("- 材料・外注・図面確認など**社外要因による停止**が含まれます。社内努力だけでは解消しないため、先方への確認を最優先にしてください。")
    lines += ["", f"### 今週対応すべき案件トップ{len(top)}", ""]
    for i, (_, r) in enumerate(top.iterrows(), 1):
        due_txt = f"{r.due_date:%m/%d}（残り{r.days_to_due}日）" if r.days_to_due >= 0 else f"{r.due_date:%m/%d}（{-r.days_to_due}日超過）"
        lines.append(f"**{i}. {r.order_id}｜{r.customer}｜{r['product']} {int(r.qty)}個** — 重要度 **{r['rank']}**（スコア{r.score}）")
        impact_txt = f"+{int(r.impact_days)}日" if "impact_days" in r.index else "-"
        lines.append(f"- 納期：{due_txt}　現在工程：{r.current_process}　納期への影響：{impact_txt}")
        lines.append("- 理由：")
        for rs in r.reasons.split(" ／ "):
            lines.append(f"  - {rs}")
        lines.append("- 推奨アクション：")
        for a in recommend_actions(r, th):
            lines.append(f"  - {a}")
        lines.append("")
    lines += ["### 今週の進め方（提案）",
              "1. 朝会で上記案件の担当者・期限を決める",
              "2. 社外要因（外注・材料・図面）は当日中に先方へ連絡し、回答期限を設定",
              "3. 納期が7日以内の案件は、遅れが見込まれる時点で顧客へ事前連絡（信頼維持）",
              "4. 進捗の更新（last_updated）を毎日入力し、停滞の見逃しを防ぐ", "",
              "_※本レポートはルールベースで自動生成しています（AI APIキー未設定時）。_"]
    return "\n".join(lines)


def build_llm_prompt(o: pd.DataFrame, today, top_n: int = 8) -> str:
    flagged = o[o["issues"] != "問題なし"].head(top_n)
    items = [dict(案件=r.order_id, 顧客=r.customer, 品名=r["product"], 数量=int(r.qty),
                  納期=f"{r.due_date:%Y-%m-%d}", 納期まで日数=r.days_to_due, 重要度=r["rank"],
                  スコア=r.score, 現在工程=r.current_process, フラグ=r.issues, 理由=r.reasons,
                  前兆=r.precursors, 前兆の理由=r.precursor_reasons,
                  納期への影響日数=int(r.impact_days),
                  完了見込み=("" if pd.isna(r.forecast_date) else f"{r.forecast_date:%Y-%m-%d}"))
             for _, r in flagged.iterrows()]
    return (f"あなたは日本の中小製造業（町工場）の生産管理に詳しいコンサルタントです。\n"
            f"基準日は{pd.Timestamp(today):%Y-%m-%d}です。以下はルールで抽出した要注意案件（重要度順）です。\n"
            f"工場長向けに『今週手を打つべき案件と理由』のレポートを日本語のMarkdownで作成してください。\n"
            f"構成：1) 全体所見（3行以内） 2) 今週対応すべき案件トップ5（各案件に理由と具体的な推奨アクション："
            f"外注先へ確認、工程の前倒し、顧客へ事前連絡など） 3) 今週の進め方。データにない事実は創作しないこと。\n\n"
            f"全案件数：{len(o)}件\n要注意案件：\n{json.dumps(items, ensure_ascii=False, indent=1)}")


def llm_provider():
    if os.environ.get("XAI_API_KEY"):
        return ("xAI", "https://api.x.ai/v1/chat/completions", os.environ["XAI_API_KEY"],
                os.environ.get("LLM_MODEL", "grok-4"))
    if os.environ.get("OPENAI_API_KEY"):
        return ("OpenAI", "https://api.openai.com/v1/chat/completions", os.environ["OPENAI_API_KEY"],
                os.environ.get("LLM_MODEL", "gpt-4o-mini"))
    return None


def llm_report(prompt: str, timeout: int = 90) -> str:
    import requests
    prov = llm_provider()
    if not prov:
        raise RuntimeError("APIキーが設定されていません")
    _, url, key, model = prov
    resp = requests.post(url, headers={"Authorization": f"Bearer {key}"}, timeout=timeout, json={
        "model": model, "temperature": 0.3,
        "messages": [{"role": "system", "content": "あなたは製造業の生産管理コンサルタントです。簡潔で実務的な日本語で書きます。"},
                     {"role": "user", "content": prompt}]})
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]

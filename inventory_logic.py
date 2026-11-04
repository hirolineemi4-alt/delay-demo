"""在庫の見える化デモのロジック（Streamlitに依存しない純粋関数）。

マスタ登録なし（L0）：手元の在庫表（Excel/CSV）をそのまま読み込み、列名のゆれを吸収して
欠品リスク・過剰在庫を判定する。
"""
from __future__ import annotations
import io
import json
import re
import unicodedata
from dataclasses import dataclass
import pandas as pd

DAYS_PER_MONTH = 30  # 1か月 = 30日（暦日、設計書の方針に合わせる）

# 正規化後の列名で照合（NFKC・空白/記号除去・小文字化）。先に書いたものほど優先。
ALIASES: dict[str, list[str]] = {
    "code": ["品番", "品目コード", "品目番号", "品目cd", "部品番号", "部品コード", "商品コード", "製品コード",
             "型番", "コード", "itemcode", "itemno", "partno", "partnumber", "sku", "code"],
    "name": ["品名", "品目名", "部品名", "商品名", "製品名", "名称", "品目", "itemname", "name", "description"],
    "stock": ["現在庫", "現在庫数", "現在在庫", "現在在庫数", "在庫数", "在庫数量", "在庫", "手持在庫", "手持ち在庫",
              "手持数", "理論在庫", "実在庫", "stock", "onhand", "qtyonhand", "inventory"],
    "monthly": ["月平均出荷", "月平均出荷数", "月平均出荷量", "月間出荷", "月間出荷数", "月平均使用量", "月平均使用数",
                "月間使用量", "月使用量", "月平均消費", "月平均消費量", "月平均払出", "月平均払出数", "平均出荷月",
                "月平均", "monthlyusage", "monthlydemand", "monthlyshipment"],
    "daily": ["日平均出荷", "日平均出荷数", "日平均使用量", "1日平均出荷", "1日あたり出荷", "1日あたり使用量",
              "dailyusage", "dailydemand"],
    "reorder": ["発注点", "発注点数", "安全在庫", "安全在庫数", "最低在庫", "最小在庫", "下限在庫", "reorderpoint",
                "safetystock", "minstock"],
    "incoming": ["入荷予定", "入荷予定数", "入庫予定", "入庫予定数", "発注残", "注残", "発注済未入荷", "未入荷",
                 "onorder", "incoming"],
    "price": ["単価", "在庫単価", "仕入単価", "原価", "標準原価", "購入単価", "unitprice", "unitcost", "price", "cost"],
}
LABELS = {"code": "品番", "name": "品名", "stock": "現在庫", "monthly": "月平均出荷", "daily": "日平均出荷",
          "recent": "直近の出荷数", "reorder": "発注点／安全在庫", "incoming": "入荷予定", "price": "単価"}
# 「直近3ヶ月出荷」「過去6か月の出荷数」などは期間（月数）を読み取って月平均に換算する
RECENT_RE = re.compile(r"(?:直近|過去|最近)?(\d+)(?:ヶ|か|カ|ケ|箇)?月(?:間)?(?:の)?(?:合計)?(?:出荷|使用|払出|販売|消費)")
RECENT_PLAIN = ["直近の出荷数", "直近出荷数", "直近出荷", "直近の出荷", "最近の出荷数"]  # 期間不明 → 1か月分とみなす

STATUS_ORDER = ["欠品危険", "欠品注意", "過剰在庫", "動きなし", "適正"]
STATUS_ICON = {"欠品危険": "🔴 欠品危険", "欠品注意": "🟡 欠品注意", "過剰在庫": "🔵 過剰在庫",
               "動きなし": "⚪ 動きなし", "適正": "🟢 適正"}


@dataclass
class InvThresholds:
    shortage_days: int = 14       # 在庫日数がこれ以下なら欠品リスク
    overstock_months: float = 3.0  # 在庫月数がこれを超えたら過剰在庫


def _norm(s) -> str:
    s = unicodedata.normalize("NFKC", str(s)).lower()
    return re.sub(r"[\s_\-・/（）()\[\]【】:：「」.。,、]", "", s)


def _to_num(s: pd.Series) -> pd.Series:
    t = s.astype(str).map(lambda v: unicodedata.normalize("NFKC", v))
    t = t.str.replace(r"[,¥￥円個本枚kgKG\s]", "", regex=True).replace({"": None, "-": None, "nan": None, "None": None})
    return pd.to_numeric(t, errors="coerce")


def map_columns(columns) -> tuple[dict[str, str], dict]:
    """列名 → 役割 の対応を推定する。戻り値：({役割: 元の列名}, {'recent_months': n})"""
    normed = {c: _norm(c) for c in columns}
    found: dict[str, str] = {}
    extra = {}
    for role, names in ALIASES.items():
        for alias in names:  # 完全一致を優先
            hit = next((c for c, n in normed.items() if n == _norm(alias) and c not in found.values()), None)
            if hit is not None:
                found[role] = hit
                break
    if "monthly" not in found and "daily" not in found:
        for c, n in normed.items():
            if c in found.values():
                continue
            m = RECENT_RE.search(n)
            if m:
                found["recent"] = c
                extra["recent_months"] = int(m.group(1))
                break
            if n in [_norm(x) for x in RECENT_PLAIN]:
                found["recent"] = c
                extra["recent_months"] = 1
                break
    for role, names in ALIASES.items():  # 部分一致（例：「現在庫数(個)」「単価（円）」）
        if role in found:
            continue
        for alias in names:
            a = _norm(alias)
            if len(a) < 2:
                continue
            hit = next((c for c, n in normed.items() if a in n and c not in found.values()), None)
            if hit is not None:
                found[role] = hit
                break
    return found, extra


def _header_score(values) -> int:
    found, _ = map_columns([v for v in values if str(v).strip() and str(v) != "nan"])
    return len(found)


def _read_any(raw: bytes, filename: str) -> pd.DataFrame:
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm", ".xls")):
        book = pd.read_excel(io.BytesIO(raw), sheet_name=None, header=None, dtype=str)
        # 在庫表らしいシート（列名の一致が最も多いシート）を採用
        best = max(book.values(), key=lambda d: max((_header_score(d.iloc[i].tolist()) for i in range(min(15, len(d)))), default=0))
        return best
    for enc in ("utf-8-sig", "cp932"):
        try:
            return pd.read_csv(io.BytesIO(raw), encoding=enc, dtype=str, header=None, keep_default_na=False)
        except UnicodeDecodeError:
            continue
    raise ValueError("文字コードを判別できませんでした（UTF-8 または Shift_JIS で保存してください）")


def read_inventory(src, filename: str | None = None) -> tuple[pd.DataFrame, dict]:
    """在庫表（Excel/CSV）を読み込み、標準列（code,name,stock,monthly,reorder,incoming,price）に揃える。

    表の上にタイトル行があっても、列名らしい行を自動で探す。戻り値：(標準化DataFrame, 読み込み情報)
    """
    if hasattr(src, "read"):
        raw = src.read()
        filename = filename or getattr(src, "name", "")
    else:
        filename = filename or str(src)
        with open(src, "rb") as f:
            raw = f.read()
    grid = _read_any(raw, filename).fillna("")
    if grid.empty:
        raise ValueError("表が空です")
    scores = [_header_score(grid.iloc[i].tolist()) for i in range(min(15, len(grid)))]
    hdr = max(range(len(scores)), key=lambda i: scores[i])
    cols = [str(c).strip() for c in grid.iloc[hdr].tolist()]
    df = grid.iloc[hdr + 1:].copy()
    df.columns = cols
    df = df.loc[:, [c for c in df.columns if c and c != "nan"]]
    df = df.loc[:, ~pd.Index(df.columns).duplicated()]
    mapping, extra = map_columns(df.columns)

    missing = []
    if "stock" not in mapping:
        missing.append("現在庫（在庫数）")
    if not {"monthly", "daily", "recent"} & mapping.keys():
        missing.append("月平均出荷（または 直近の出荷数／日平均出荷）")
    if "code" not in mapping and "name" not in mapping:
        missing.append("品番 または 品名")
    if missing:
        raise ValueError("必要な列が見つかりません：" + "、".join(missing) +
                         f"。読み込んだ列名：{', '.join(map(str, df.columns))}")

    out = pd.DataFrame(index=df.index)
    out["code"] = df[mapping["code"]].astype(str).str.strip() if "code" in mapping else ""
    out["name"] = df[mapping["name"]].astype(str).str.strip() if "name" in mapping else ""
    out["stock"] = _to_num(df[mapping["stock"]])
    if "monthly" in mapping:
        out["monthly"] = _to_num(df[mapping["monthly"]])
    elif "daily" in mapping:
        out["monthly"] = _to_num(df[mapping["daily"]]) * DAYS_PER_MONTH
    else:
        out["monthly"] = _to_num(df[mapping["recent"]]) / extra.get("recent_months", 1)
    for role in ["reorder", "incoming", "price"]:
        out[role] = _to_num(df[mapping[role]]) if role in mapping else pd.NA
    # 合計行・空行を除外（品番も品名も空、または在庫が数値でない行）
    blank_id = (out["code"].isin(["", "nan"])) & (out["name"].isin(["", "nan"]))
    total_row = out["name"].str.contains("合計|小計|total", case=False, na=False) | out["code"].str.contains("合計|小計", na=False)
    out = out[~blank_id & ~total_row & out["stock"].notna()].reset_index(drop=True)
    if out.empty:
        raise ValueError("在庫数が数値として読み取れる行がありません")
    out["monthly"] = out["monthly"].fillna(0).clip(lower=0)
    out["incoming"] = pd.to_numeric(out["incoming"], errors="coerce")
    info = {"filename": filename, "header_row": hdr + 1, "rows": len(out),
            "mapping": {LABELS[k]: v for k, v in mapping.items()},
            "recent_months": extra.get("recent_months"),
            "has_price": "price" in mapping and out["price"].notna().any(),
            "has_reorder": "reorder" in mapping and out["reorder"].notna().any(),
            "has_incoming": "incoming" in mapping and out["incoming"].notna().any()}
    return out, info


def analyze_inventory(df: pd.DataFrame, th: InvThresholds) -> pd.DataFrame:
    """品目ごとに在庫日数・在庫月数・判定を付与し、要注意順に並べる。"""
    d = df.copy()
    d["incoming"] = pd.to_numeric(d["incoming"], errors="coerce").fillna(0)
    d["daily"] = d["monthly"] / DAYS_PER_MONTH
    moving = d["daily"] > 0
    d["cover_days"] = (d["stock"] / d["daily"]).where(moving)                      # 現在庫だけで何日もつか
    d["cover_days_in"] = ((d["stock"] + d["incoming"]) / d["daily"]).where(moving)  # 入荷予定込み
    d["cover_months"] = d["cover_days_in"] / DAYS_PER_MONTH
    reorder = pd.to_numeric(d["reorder"], errors="coerce")
    d["below_reorder"] = reorder.notna() & (d["stock"] <= reorder)

    status, reasons = [], []
    for _, r in d.iterrows():
        rs = []
        if r["daily"] <= 0:
            st = "動きなし" if r["stock"] > 0 else "適正"
            if r["stock"] > 0:
                rs.append("出荷実績なし（不動在庫の可能性）")
        elif r["cover_days_in"] <= th.shortage_days:
            st = "欠品危険"
            rs.append(f"入荷予定を含めても約{r.cover_days_in:.0f}日分（{th.shortage_days}日以下）")
        elif r["cover_days"] <= th.shortage_days:
            st = "欠品注意"
            rs.append(f"現在庫は約{r.cover_days:.0f}日分。入荷予定{int(r.incoming)}が間に合えば回避")
        elif r["cover_months"] > th.overstock_months:
            st = "過剰在庫"
            rs.append(f"約{r.cover_months:.1f}か月分（{th.overstock_months:g}か月超）")
        else:
            st = "適正"
        if r["below_reorder"] and st in ("適正", "過剰在庫"):
            if st == "適正":
                st = "欠品注意"
            rs.append(f"発注点（{int(r.reorder)}）を下回っている")
        elif r["below_reorder"]:
            rs.append(f"発注点（{int(r.reorder)}）以下")
        status.append(st)
        reasons.append(" ／ ".join(rs))
    d["status"] = status
    d["reason"] = reasons

    # 過剰分（しきい値の月数を超える数量）と金額
    keep = d["monthly"] * th.overstock_months
    d["excess_qty"] = ((d["stock"] + d["incoming"]) - keep).clip(lower=0).where(d["status"].isin(["過剰在庫", "動きなし"]), 0)
    d["excess_qty"] = d["excess_qty"].clip(upper=d["stock"])  # 入荷予定分は除き、手元にある分だけを金額化
    price = pd.to_numeric(d["price"], errors="coerce")
    d["stock_value"] = d["stock"] * price
    d["excess_value"] = d["excess_qty"] * price
    # 欠品までの目安日（何日後に在庫ゼロか）
    d["_o"] = d["status"].map({s: i for i, s in enumerate(STATUS_ORDER)})
    d["_k"] = d["cover_days_in"].where(d["status"].isin(["欠品危険", "欠品注意"]),
                                       -d["excess_value"].fillna(d["excess_qty"]))
    d = d.sort_values(["_o", "_k"]).drop(columns=["_o", "_k"]).reset_index(drop=True)
    return d


def kpis(d: pd.DataFrame) -> dict:
    risk = d["status"].isin(["欠品危険", "欠品注意"])
    over = d["status"].isin(["過剰在庫", "動きなし"])
    has_price = d["price"].notna().any() if "price" in d else False
    return {"items": len(d), "risk": int(risk.sum()), "danger": int((d["status"] == "欠品危険").sum()),
            "over": int(over.sum()), "dead": int((d["status"] == "動きなし").sum()),
            "excess_value": float(d["excess_value"].sum()) if has_price else None,
            "stock_value": float(d["stock_value"].sum()) if has_price else None}


def yen(v) -> str:
    if v is None or pd.isna(v):
        return "-"
    return f"{v / 10000:,.1f}万円" if abs(v) >= 100000 else f"{v:,.0f}円"


def _label(r) -> str:
    return f"{r.code}｜{r['name']}" if r.code and r.code != "nan" else str(r["name"])


# ---------------- レポート ----------------
def inventory_rule_report(d: pd.DataFrame, th: InvThresholds, top_n: int = 5) -> str:
    k = kpis(d)
    risk = d[d["status"].isin(["欠品危険", "欠品注意"])].head(top_n)
    over = d[d["status"].isin(["過剰在庫", "動きなし"])].sort_values(
        ["excess_value" if k["excess_value"] is not None else "excess_qty"], ascending=False).head(top_n)
    lines = ["## 在庫の見える化レポート", "", "### 全体所見",
             f"- 全{k['items']}品目のうち、**欠品リスク {k['risk']}品目**（うち危険 {k['danger']}）、"
             f"**過剰在庫 {k['over']}品目**（うち出荷実績なし {k['dead']}）です。"]
    if k["excess_value"] is not None:
        lines.append(f"- 過剰分の在庫金額は **約{yen(k['excess_value'])}**（在庫金額合計 約{yen(k['stock_value'])}）。"
                     "現金が在庫として眠っている状態です。")
    lines.append(f"- 判定基準：在庫日数 {th.shortage_days}日以下＝欠品リスク、在庫 {th.overstock_months:g}か月分超＝過剰（1か月＝{DAYS_PER_MONTH}日）。")
    lines += ["", f"### 先に手配すべき品目（欠品リスク上位{len(risk)}）", ""]
    if risk.empty:
        lines.append("- 該当なし")
    for i, (_, r) in enumerate(risk.iterrows(), 1):
        lines.append(f"**{i}. {_label(r)}** — {STATUS_ICON[r.status]}")
        lines.append(f"- 現在庫 {r.stock:,.0f}／月平均出荷 {r.monthly:,.0f} → 約{r.cover_days:.0f}日分"
                     + (f"（入荷予定 {r.incoming:,.0f} を含め約{r.cover_days_in:.0f}日分）" if r.incoming else ""))
        act = ("入荷予定日を仕入先に確認し、出荷予定に間に合うか確認" if r.status == "欠品注意" and r.incoming
               else "本日中に発注（または前倒し納入を依頼）。必要なら出荷の優先順位を調整")
        lines.append(f"- 推奨アクション：{act}")
        lines.append("")
    lines += [f"### 減らせる在庫（過剰在庫上位{len(over)}）", ""]
    if over.empty:
        lines.append("- 該当なし")
    for i, (_, r) in enumerate(over.iterrows(), 1):
        val = f"、過剰分 約{yen(r.excess_value)}" if pd.notna(r.excess_value) else ""
        months = "出荷実績なし" if r.status == "動きなし" else f"約{r.cover_months:.1f}か月分"
        lines.append(f"**{i}. {_label(r)}** — {STATUS_ICON[r.status]}")
        lines.append(f"- 現在庫 {r.stock:,.0f}（{months}）、過剰分 {r.excess_qty:,.0f}{val}")
        act = ("今後の使用予定を確認し、転用・処分・返品を検討" if r.status == "動きなし"
               else "次回発注を見送る／発注ロットを小さくする。入荷予定があれば延期を相談")
        lines.append(f"- 推奨アクション：{act}")
        lines.append("")
    lines += ["### 次の一歩（提案）",
              "1. 欠品リスクの品目は今日中に発注・納期確認（担当と期限を決める）",
              "2. 過剰在庫の品目は次回の発注を止め、発注点・ロットを見直す",
              "3. この在庫表を週1回読み込むだけで、同じ一覧が更新されます", "",
              "_※本レポートはルールベースで自動生成しています（AI APIキー未設定時）。_"]
    return "\n".join(lines)


def build_inventory_prompt(d: pd.DataFrame, th: InvThresholds, top_n: int = 10) -> str:
    k = kpis(d)
    def rows(x):
        return [dict(品番=r.code, 品名=r["name"], 判定=r.status, 現在庫=r.stock, 月平均出荷=r.monthly,
                     入荷予定=r.incoming, 在庫日数=None if pd.isna(r.cover_days) else round(r.cover_days, 1),
                     在庫月数_入荷込=None if pd.isna(r.cover_months) else round(r.cover_months, 1),
                     過剰数量=r.excess_qty, 過剰金額=None if pd.isna(r.excess_value) else round(r.excess_value),
                     理由=r.reason) for _, r in x.iterrows()]
    risk = d[d["status"].isin(["欠品危険", "欠品注意"])].head(top_n)
    over = d[d["status"].isin(["過剰在庫", "動きなし"])].head(top_n)
    return (f"あなたは日本の中小製造業（多品種少量生産の町工場）の在庫管理に詳しいコンサルタントです。\n"
            f"以下は在庫表からルールで判定した結果です（欠品リスク：在庫日数{th.shortage_days}日以下、"
            f"過剰：{th.overstock_months:g}か月分超、1か月={DAYS_PER_MONTH}日）。\n"
            f"社長・工場長向けに『在庫の見える化レポート』を日本語のMarkdownで簡潔に作成してください。\n"
            f"構成：1) 全体所見（3行以内、過剰在庫金額があれば言及） 2) 先に手配すべき品目（上位5、理由と具体的アクション） "
            f"3) 減らせる在庫（上位5、理由と具体的アクション） 4) 次の一歩。データにない事実は創作しないこと。\n\n"
            f"KPI：{json.dumps(k, ensure_ascii=False)}\n欠品リスク：\n{json.dumps(rows(risk), ensure_ascii=False, indent=1, default=str)}\n"
            f"過剰在庫：\n{json.dumps(rows(over), ensure_ascii=False, indent=1, default=str)}")

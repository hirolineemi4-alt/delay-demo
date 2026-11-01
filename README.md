# 納期遅れ・停滞の早期検知デモ

町工場向けの営業デモです。工程表CSVを読み、遅れ・着手遅れ・停滞・納期リスク・保留をルールで拾い、今週手を打つ案件を文章にします。

データはすべて架空です（`sample_orders.csv`）。APIキーはリポジトリに含まれません。

## 起動

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

ブラウザで開くと、基準日 2026-11-10 のサンプル（28受注・123工程）が表示されます。未アップロードのときは `app.py` と同じディレクトリの `sample_orders.csv` を使います。

```bash
python test_logic.py
python test_app.py
```

サンプルを作り直す場合:

```bash
python generate_sample.py
```

## 画面

- **要注意案件一覧** — 重要度・スコア順。問題のない案件は初期状態では隠し、CSVで書き出せます。
- **案件ドリルダウン** — 該当理由、予定と実績のガント、工程表。
- **AIレポート** — キーが無いときはルールベースの文章です。`XAI_API_KEY`（優先）または `OPENAI_API_KEY` があると「AIでレポートを作成」が出ます。モデルは `LLM_MODEL` で上書きできます。

Streamlit Community Cloud では、これらのキーをアプリの Secrets に置いてください。`.streamlit/secrets.toml` は git に含めません。

## 自社CSV

1行が1工程です。必須列は `order_id`, `customer`, `product`, `qty`, `due_date`, `process_seq`, `process_name`, `planned_start`, `planned_end`, `actual_start`, `actual_end`, `status`, `last_updated`, `note` です。文字コードは UTF-8（BOM可）または Shift_JIS。`status` は `未着手` / `着手中` / `完了` / `保留`。

検知ルールとスコアの詳細は `DESIGN.md` を参照してください。

"""Streamlit AppTest で画面がサンプルデータどおり描画されることを確認する。"""

from __future__ import annotations

import os
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parent / "app.py"


def test_sample_dashboard() -> None:
    os.environ.pop("XAI_API_KEY", None)
    os.environ.pop("OPENAI_API_KEY", None)
    app = AppTest.from_file(str(APP), default_timeout=60)
    app.run()
    if app.exception:
        raise AssertionError(app.exception)
    metrics = {metric.label: str(metric.value) for metric in app.metric}
    assert metrics["全案件数"] == "28"
    assert metrics["要注意案件数"] == "11"
    assert metrics["高リスク件数"] == "5"
    assert metrics["停滞工程数"] == "3"
    markdown = "\n".join(block.value for block in app.markdown)
    captions = "\n".join(block.value for block in app.caption)
    attention = app.dataframe[0].value
    assert "納期遅れ・停滞の早期検知デモ" in markdown
    assert "sample_orders.csv" in captions
    assert "2026-11-10" in captions
    assert list(attention["受注No"])[0] == "ORD-1010"
    assert int(attention["スコア"].iloc[0]) == 180
    assert len(attention) == 11
    assert "ORD-1010" in markdown
    assert "ルールベース生成" in markdown
    assert any(button.label == "抽出結果をCSVでダウンロード" for button in app.download_button)
    assert not any(button.label == "AIでレポートを作成" for button in app.button)


def main() -> None:
    test_sample_dashboard()
    print("test_app.py: 1 passed")


if __name__ == "__main__":
    main()

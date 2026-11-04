"""エントリーポイント（ルーター）。

- ルートURL（/）        … 納期遅れ・停滞の早期検知デモ（A：従来どおり）
- /inventory           … 在庫の見える化デモ（B）
ナビゲーションは非表示（position="hidden"）。ルートの訪問者には在庫デモへのリンクは表示されない。
"""
import streamlit as st

pg = st.navigation(
    [
        st.Page("delay_demo.py", title="納期遅れ・停滞 早期検知デモ", icon="🏭", default=True),
        st.Page("inventory_demo.py", title="在庫の見える化デモ", icon="📦", url_path="inventory"),
    ],
    position="hidden",
)
pg.run()

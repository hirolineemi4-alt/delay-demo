from streamlit.testing.v1 import AppTest
at = AppTest.from_file("app.py", default_timeout=60).run()
assert not at.exception, at.exception
body = " ".join(m.value for m in at.markdown)
assert "今朝見る3件" in body, body[:400]
assert "納期への影響" in body
assert "提案" in body and "確認した" in [b.label for b in at.button]
print("tabs ok; markdown blocks:", len(at.markdown))
# 提案を人が確認する
at.button[0].click().run()
assert not at.exception, at.exception
body = " ".join(m.value for m in at.markdown)
assert "確認済み" in body
# 別案件を選んでドリルダウン
at.selectbox[0].select("ORD-1019").run(); assert not at.exception, at.exception
at.toggle[0].set_value(True).run(); assert not at.exception, at.exception
at.number_input[0].set_value(5).run(); assert not at.exception, at.exception
print("APP TEST PASSED")

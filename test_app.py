from streamlit.testing.v1 import AppTest
at = AppTest.from_file("app.py", default_timeout=60).run()
assert not at.exception, at.exception
print("tabs ok; markdown blocks:", len(at.markdown))
# 別案件を選んでドリルダウン
at.selectbox[0].select("ORD-1019").run(); assert not at.exception, at.exception
at.toggle[0].set_value(True).run(); assert not at.exception, at.exception
at.number_input[0].set_value(5).run(); assert not at.exception, at.exception
print("APP TEST PASSED")

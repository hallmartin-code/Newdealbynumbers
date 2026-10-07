"""Headless run of the Streamlit app through its demo flow."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def _button(at, label):
    return next(b for b in at.button if b.label == label)


def test_demo_flow_generates_one_page_pdf(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert any("No ANTHROPIC_API_KEY" in w.value for w in at.sidebar.warning)

    _button(at, "Use fictional sample deck").click().run()
    assert any("sample_deck.pptx" in s.value for s in at.success)
    analyze = _button(at, "Analyze deck")
    assert analyze.disabled  # no key, no consent

    _button(at, "Load demo analysis (fictional sample data)").click().run()
    assert not at.exception
    assert any("DEMO MODE" in w.value for w in at.warning)
    assert any(m.label == "Estimates awaiting approval" and m.value == "3" for m in at.metric)

    _button(at, "Generate one-page PDF").click().run()
    assert not at.exception
    assert any("exactly one page" in s.value for s in at.success)
    assert any("unapproved estimate" in s.value for s in at.success)


def test_clear_session_resets_state():
    at = AppTest.from_file(APP, default_timeout=60).run()
    _button(at, "Load demo analysis (fictional sample data)").click().run()
    assert at.session_state["base"] is not None
    _button(at, "Clear session").click().run()
    assert at.session_state["base"] is None
    assert at.session_state["deck"] is None

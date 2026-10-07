"""Analysis pipeline tests with a fake API client (no network, no credentials)."""

import json
from types import SimpleNamespace

import pytest

from onepager.analysis import AnalysisError, analyze_deck, chunk_pages, extract_json
from onepager.config import Settings
from onepager.demo import DEMO_ANALYSIS_PATH
from onepager.extraction import DeckContent, DeckPage


class _Stream:
    def __init__(self, response):
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.response


class FakeClient:
    """Returns queued text responses and records the prompts it was sent."""

    def __init__(self, texts):
        self.texts = list(texts)
        self.prompts = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, messages, **kwargs):
        self.prompts.append(messages[-1]["content"])
        text = self.texts.pop(0)
        if isinstance(text, Exception):
            raise text
        return _Stream(SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn"))


DEMO_JSON = DEMO_ANALYSIS_PATH.read_text(encoding="utf-8")


def _deck(n=3, size=50):
    return DeckContent("d.pdf", "pdf", [DeckPage(f"Page {i}", i, f"fact {i} " + "x" * size) for i in range(1, n + 1)])


def test_valid_response_parsed_and_meta_set():
    client = FakeClient(["```json\n" + DEMO_JSON + "\n```"])
    a = analyze_deck(_deck(), Settings(model="test-model"), client=client)
    assert a.company_name == "Northpeak Cold Chain"
    assert a.meta["model"] == "test-model" and a.meta["demo"] is False
    assert "=== Page 2 ===" in client.prompts[0]


def test_malformed_response_is_repaired_once():
    bad = json.loads(DEMO_JSON)
    del bad["sections"]["exit"]
    client = FakeClient([json.dumps(bad), DEMO_JSON])
    a = analyze_deck(_deck(), Settings(model="m"), client=client)
    assert a.sections.exit.summary
    assert "does not validate" in client.prompts[1]
    assert "sections.exit" in client.prompts[1]


def test_unrepairable_response_raises_clean_error():
    client = FakeClient(["not json at all", "still not json"])
    with pytest.raises(AnalysisError, match="could not be validated"):
        analyze_deck(_deck(), Settings(model="m"), client=client)


def test_api_error_becomes_analysis_error():
    import anthropic
    import httpx

    err = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))
    with pytest.raises(AnalysisError, match="Could not reach"):
        analyze_deck(_deck(), Settings(model="m"), client=FakeClient([err]))


def test_long_deck_chunked_with_refs_preserved():
    deck = _deck(n=6, size=400)
    notes = [json.dumps({"facts": [{"ref": "Page 1", "text": "ARR $1M"}, {"ref": "Page 2", "text": "14 customers"}]}),
             json.dumps({"facts": [{"ref": "Page 4", "text": "Raising $3M"}, {"ref": "Page 99", "text": "bogus"}]}),
             json.dumps({"facts": [{"ref": "Page 6", "text": "TAM $5B"}]})]
    settings = Settings(model="m", max_chars_per_request=2000)  # 1000-char chunks: 2 pages each
    client = FakeClient(notes + [DEMO_JSON])
    a = analyze_deck(deck, settings, client=client)
    final_prompt = client.prompts[-1]
    assert "=== Page 4 ===\n- Raising $3M" in final_prompt
    assert "Page 99 (unverified ref)" in final_prompt
    assert a.meta["chunked"] is True
    assert len(chunk_pages(deck, 500)) == 6


def test_exit_and_research_defaults_in_prompt():
    client = FakeClient([DEMO_JSON])
    analyze_deck(_deck(), Settings(model="m"), client=client)
    assert "Exit scenarios were NOT requested" in client.prompts[0]
    assert "Use only the deck" in client.prompts[0]


def test_extract_json_handles_prose():
    assert extract_json('Here you go: {"a": 1} thanks') == '{"a": 1}'
    with pytest.raises(ValueError):
        extract_json("no braces")

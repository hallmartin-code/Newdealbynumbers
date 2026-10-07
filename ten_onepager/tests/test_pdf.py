import re

import pymupdf
import pytest
from conftest import make

from onepager.evidence import pending_estimates
from onepager.export import export_payload
from onepager.pdf import CONFIDENTIAL, ContentOverflowError, render_pdf
from onepager.validation import validate

SMALL_TEXT_OK = re.compile(r"^\s*(\[.*\]|EST\.|FORECAST|PROPOSED|calc\.)?\s*$")


def _doc(pdf: bytes):
    return pymupdf.open(stream=pdf, filetype="pdf")


def _text(pdf: bytes) -> str:
    with _doc(pdf) as d:
        return "\n".join(p.get_text() for p in d)


def test_exactly_one_page_with_all_sections_and_footer(demo):
    result = render_pdf(demo)
    with _doc(result.pdf) as d:
        assert d.page_count == 1
    text = _text(result.pdf)
    for heading in ("PROBLEM", "SOLUTION", "TEAM", "TRACTION", "MARKET SIZE", "COMPETITIVE ADVANTAGE",
                    "FUNDRAISE", "USE OF FUNDS", "EXIT"):
        assert heading in text
    assert CONFIDENTIAL in text
    assert "Compiled on" in text and "TEN Capital Network" in text


def test_unapproved_estimates_excluded_then_included_after_approval(demo):
    text = _text(render_pdf(demo).pdf)
    assert "$33M" not in text            # SOM estimate
    assert "$1.1M estimated" not in text  # savings estimate
    assert "EST." not in text
    for _, item in pending_estimates(demo):
        item.approved = True
    text = _text(render_pdf(demo).pdf)
    assert "$33M" in text and "EST." in text


def test_conflicting_and_missing_stay_labelled(demo):
    text = _text(render_pdf(demo).pdf)
    assert "$1.4M" not in text            # conflicting ARR value not printed
    assert "Not provided" in text
    assert "Lead investor" in text


def test_reported_figures_carry_slide_references(demo):
    text = _text(render_pdf(demo).pdf)
    assert "[S8]" in text and "[S4]" in text


def test_forecasts_are_labelled(demo):
    assert "FORECAST" in _text(render_pdf(demo).pdf)


def test_body_text_never_below_9pt(demo):
    for _, item in pending_estimates(demo):
        item.approved = True
    result = render_pdf(demo)
    with _doc(result.pdf) as d:
        page = d[0]
        top = page.rect.height - result.columns_top
        bottom = page.rect.height - result.columns_bottom
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    y = span["bbox"][1]
                    if top <= y <= bottom and span["size"] < 8.95:
                        assert SMALL_TEXT_OK.match(span["text"]), (span["text"], span["size"])


def test_long_content_is_shortened_to_fit(demo_dict):
    long = "This sentence adds detail about the company that an investor may find useful. " * 4
    for sec in demo_dict["sections"].values():
        sec["summary"] = long
        sec["points"] = [{"text": long, "evidence": "reported", "source_refs": ["Slide 2"]} for _ in range(3)]
    result = render_pdf(make(demo_dict))
    assert result.trim_level > 0
    with _doc(result.pdf) as d:
        assert d.page_count == 1


def test_impossible_content_reports_sections_to_shorten(demo_dict):
    huge = "Very long first sentence " + "with many words " * 400 + "."
    demo_dict["sections"]["traction"]["summary"] = huge
    with pytest.raises(ContentOverflowError) as exc:
        render_pdf(make(demo_dict))
    assert exc.value.sections[0][0] == "Traction"
    assert "Traction" in str(exc.value)


def test_json_export_keeps_assumptions(demo):
    for _, item in pending_estimates(demo):
        item.approved = True
    payload = export_payload(demo, validate(demo), {"pages": 1})
    approved = payload["estimates"]["approved_for_pdf"]
    assert any(a["item"] == "som" and a["assumptions"] for a in approved)
    som = next(m for m in payload["analysis"]["sections"]["market_size"]["metrics"] if m["id"] == "som")
    assert som["approved"] and som["assumptions"] and som["confidence"] == "low"

"""Demo mode: a fictional, hand-written analysis of samples/sample_deck.pptx.

Used when no API key is configured, or when the user asks for demo data. The
result is marked as demo so the app and the PDF label it clearly.
"""

from __future__ import annotations

from .config import SAMPLES_DIR
from .models import Analysis

DEMO_ANALYSIS_PATH = SAMPLES_DIR / "sample_analysis.json"
DEMO_DECK_PATH = SAMPLES_DIR / "sample_deck.pptx"


def load_demo_analysis() -> Analysis:
    analysis = Analysis.model_validate_json(DEMO_ANALYSIS_PATH.read_text(encoding="utf-8"))
    analysis.meta = {
        "demo": True,
        "model": "none (demo data)",
        "source_file": DEMO_DECK_PATH.name,
        "exit_scenarios_requested": False,
        "external_research": False,
    }
    return analysis

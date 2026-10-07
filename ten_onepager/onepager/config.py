"""Runtime settings, read from environment variables (and a local .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:  # optional dependency
    pass

APP_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = APP_DIR / "assets"
SAMPLES_DIR = APP_DIR / "samples"

DEFAULT_MODEL = "claude-opus-5-5"
PROVIDER_NAME = "Anthropic (Claude API)"


@dataclass
class Settings:
    model: str = os.environ.get("CLAUDE_MODEL") or DEFAULT_MODEL
    effort: str = os.environ.get("CLAUDE_EFFORT") or "medium"
    max_tokens: int = int(os.environ.get("CLAUDE_MAX_TOKENS") or 32000)
    # Decks longer than this (in characters) are condensed chunk by chunk first.
    max_chars_per_request: int = int(os.environ.get("ONEPAGER_MAX_CHARS") or 150_000)
    ocr: bool = (os.environ.get("ONEPAGER_OCR") or "").lower() in ("1", "true", "yes")
    external_research: bool = False   # always off unless the user turns it on
    exit_scenarios: bool = False      # exit valuations/returns only on request

    @staticmethod
    def api_key_present() -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY"))

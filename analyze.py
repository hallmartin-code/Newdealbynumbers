"""Stage 2 — Analysis via the Anthropic API.

Send the extracted deck content to Claude acting as an expert startup financial
specialist, and get back a numbers-first investor pitch covering nine sections:
problem, solution, team, traction, market size, competitive advantage,
fundraise, use of funds, and exit. Each section leads with headline metrics.
Where the deck gives no numbers, Claude estimates from industry benchmarks,
flags each estimate with "is_estimated", and records the basis in
"estimate_basis" so a reader can audit it.

The response shape is enforced with structured outputs (output_config.format),
so the renderer always receives the keys it expects.
"""

from __future__ import annotations

import json
import os
import sys

# Load a local .env file (if present) so ANTHROPIC_API_KEY can live there during
# development. On Railway/production the platform provides the env var and no
# .env file exists, so this is a harmless no-op. Optional dependency.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# Override with the CLAUDE_MODEL env var to swap models without a code change.
MODEL = os.environ.get("CLAUDE_MODEL") or "claude-opus-5-5"
EFFORT = "medium"
MAX_TOKENS = 32000

# Models that accept the server-side refusal fallback ("default" routing).
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

SECTION_KEYS = (
    "problem",
    "solution",
    "team",
    "traction",
    "market_size",
    "competitive_advantage",
    "fundraise",
    "use_of_funds",
    "exit",
)

SYSTEM_PROMPT = "You are an expert startup financial specialist."

TASK_PROMPT = (
    "Review the attached deck and create the investor pitch using numbers that "
    "showcase the deal including the problem, solution, team, traction, market "
    "size, competitive advantage, fundraise, use of funds, and exit. Summarize "
    "into a one pager and estimate for sections that don't have any numbers."
)

GUIDELINES = """\
How to fill the one-pager:

- Every section leads with numbers. Give each section 2-3 "metrics" (never more than 3): a short
  "value" (e.g. "$4.2B", "38%", "3.1x", "14 mo") and a 2-5 word "label".
  Keep values under ~8 characters so they read as headline figures.
- "headline" is one sentence (max ~20 words) that states the section's key
  point and includes its most important number.
- "points" are 1-2 short supporting sentences, each quantified where possible.
- Use the deck's own figures wherever they exist, with "is_estimated": false.
- Where a section has no numbers in the deck, estimate them from industry
  benchmarks for this company's sector and stage, set "is_estimated": true,
  and explain the assumption in "estimate_basis": at most one line per
  section (6 lines max), each under 20 words, prefixed with the section
  (e.g. "Market size: SAM assumes 12% of the TAM is reachable in the US").
- Do not invent names, customers, partners, credentials, patents, or deal
  terms. Estimates are for quantities (market size, revenue, margins, runway,
  allocation, exit value, multiples). Team metrics must come from what the deck
  says about the team (headcount, years of experience, prior exits); if the
  deck says nothing quantifiable, use team size and mark it estimated.
- Exit: estimate a realistic exit value and investor return multiple from
  sector exit multiples. Name a comparable transaction only if you are
  confident it is real; otherwise describe the multiple range.
- Market size metrics are TAM, SAM, and SOM, in that order.
- Fundraise metrics cover the raise amount, valuation, and instrument or
  runway. "members" in team lists the people named in the deck.
- use_of_funds "allocation" percentages sum to 100.
- "deal_snapshot" is 5 headline figures for the whole deal, in this order:
  raise amount, valuation, current traction (revenue, users, or pilots),
  TAM, and target exit value or investor return multiple.
- "source_attribution" is "<Company> pitch deck, <Month Year>" when the
  date is known, otherwise "<Company> pitch deck".
"""


class AnalysisError(RuntimeError):
    """Raised when the API call or response parsing fails unrecoverably."""


# --------------------------------------------------------------------------- #
# Output schema (structured outputs)
# --------------------------------------------------------------------------- #
def _obj(properties: dict) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


_STR = {"type": "string"}
_BOOL = {"type": "boolean"}


def _ref(name: str) -> dict:
    return {"$ref": f"#/$defs/{name}"}


def _array(item: dict) -> dict:
    return {"type": "array", "items": item}


# Shared shapes are defined once and referenced, which keeps the compiled
# grammar small enough for the API to accept.
_SECTION_PROPS = {
    "headline": _STR,
    "metrics": _array(_ref("metric")),
    "points": _array(_ref("point")),
}

OUTPUT_SCHEMA = {
    **_obj({
        "company_name": _STR,
        "tagline": _STR,
        "sector": _STR,
        "stage": _STR,
        "source_attribution": _STR,
        "deal_snapshot": _array(_ref("metric")),
        **{key: _ref("section") for key in SECTION_KEYS if key not in ("team", "use_of_funds")},
        "team": _obj({**_SECTION_PROPS, "members": _array(
            _obj({"name": _STR, "role": _STR, "credential": _STR}))}),
        "use_of_funds": _obj({**_SECTION_PROPS, "allocation": _array(
            _obj({"category": _STR, "percent": {"type": "number"}, "is_estimated": _BOOL}))}),
        "estimate_basis": _array(_STR),
    }),
    "$defs": {
        "metric": _obj({"value": _STR, "label": _STR, "is_estimated": _BOOL}),
        "point": _obj({"text": _STR, "is_estimated": _BOOL}),
        "section": _obj(_SECTION_PROPS),
    },
}


# --------------------------------------------------------------------------- #
# API call
# --------------------------------------------------------------------------- #
def analyze(deck_text: str) -> dict:
    """Send extracted deck text to Claude and return the one-pager JSON dict."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise AnalysisError(
            "ANTHROPIC_API_KEY environment variable is not set. "
            "Export your Anthropic API key and try again."
        )

    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise AnalysisError(
            "The 'anthropic' package is not installed. Run: pip install -r requirements.txt"
        ) from exc

    client = anthropic.Anthropic()

    user_content = (
        f"{TASK_PROMPT}\n\n{GUIDELINES}\n"
        f"<deck>\n{deck_text}\n</deck>"
    )

    request = dict(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        output_config={
            "effort": EFFORT,
            "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
        },
        messages=[{"role": "user", "content": user_content}],
    )
    # On a safety-classifier decline, let the API rerun the request on a
    # fallback model instead of failing the job.
    if MODEL in _FALLBACK_MODELS:
        request.update(betas=[_FALLBACK_BETA], fallbacks="default")

    try:
        # Streamed so long analyses don't hit HTTP timeouts.
        with client.beta.messages.stream(**request) as stream:
            response = stream.get_final_message()
    except anthropic.AuthenticationError as exc:
        raise AnalysisError("The Anthropic API key was rejected.") from exc
    except anthropic.RateLimitError as exc:
        raise AnalysisError("Anthropic API rate limit hit. Try again in a minute.") from exc
    except anthropic.APIStatusError as exc:
        raise AnalysisError(f"Anthropic API request failed ({exc.status_code}): {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise AnalysisError("Could not reach the Anthropic API. Check the network.") from exc

    if response.stop_reason == "refusal":
        raise AnalysisError("The model declined to analyze this deck.")
    if response.stop_reason == "max_tokens":
        raise AnalysisError("The analysis was cut off before it finished. Try again.")

    raw = "".join(block.text for block in response.content if block.type == "text")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"\nUnparseable model response:\n{raw}\n")
        raise AnalysisError(f"Could not parse JSON from the model response: {exc}") from exc

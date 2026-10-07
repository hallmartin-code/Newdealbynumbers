"""Structured deck analysis through the Anthropic API.

The model reviews the deck as a startup financial specialist and returns JSON
matching models.Analysis. The response is validated with Pydantic; malformed
output gets one repair attempt. Long decks are condensed chunk by chunk into
referenced fact notes first, so slide references survive.

Deck contents and credentials are never logged.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from typing import Callable

from pydantic import BaseModel, ValidationError

from .config import Settings
from .extraction import DeckContent
from .models import Analysis, llm_schema

FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 5}

Progress = Callable[[str], None]


class AnalysisError(Exception):
    """Analysis failed; the message is safe to show to the user."""


SYSTEM_PROMPT = """\
You are an expert startup financial specialist preparing an investor one-pager \
for TEN Capital Network. Explain the opportunity accurately and concisely, \
supported by the strongest relevant numbers. Never exaggerate traction, invent \
facts, or present forecasts as actual results."""

RULES = """\
Cover all nine sections:
1. problem: who has the problem, its scale, its economic or operational cost.
2. solution: what the company does and its measurable customer benefit.
3. team: relevant experience, roles, prior outcomes, execution capabilities (members list).
4. traction: revenue, growth, customers, contracts, retention, pilots, regulatory progress.
5. market_size: TAM, SAM, SOM where available, each with geography, year, units and sizing method.
6. competitive_advantage: differentiation, defensibility, IP, proprietary data, distribution, measurable performance.
7. fundraise: target raise, instrument, valuation or cap, funds received, commitments, remaining amount.
8. use_of_funds: allocations, expected runway, milestones the capital should achieve.
9. exit: stated exit strategy and potential acquirer categories.

Evidence labels (required on every metric, point and allocation):
- reported: explicitly stated in the deck. source_refs must name the page/slide exactly as in the
  "=== Slide N ===" / "=== Page N ===" markers.
- calculated: derived from reported figures. Give "formula" (human readable, with inputs),
  "inputs" (metric ids) and, when possible, "expression" using metric ids and + - * / only,
  e.g. "arr_current / paying_customers".
- estimated: based on disclosed assumptions. Give "method", "assumptions", "range_low"/"range_high"
  where appropriate, and "confidence".
- not_provided: missing and cannot be responsibly estimated. Leave value null and add the gap to the
  section's "missing" list.
- conflicting: the deck gives inconsistent values. Keep value null and list every value with its
  source in "conflicting_values". Do not pick one.

Estimates:
- Estimate only when defensible inputs exist; otherwise write a concise qualitative statement and list
  the missing information. Do not force a number into every section.
- Never estimate founder credentials, actual revenue, customer counts, regulatory approvals, funding
  commitments or investment terms.
- For market size, runway, customer savings, use-of-funds allocations or exit scenarios, state the
  assumptions behind any estimate. Mark hypothetical allocations "proposed": true.
- {exit_rule}

Distinctions (set "kind" and "timeframe" precisely):
- Pilots are not paying customers; bookings, GMV and pipeline are not revenue; ARR is not revenue.
- Signed commitments, verbal interest and cash received are different kinds.
- Forecasts get timeframe "forecast"; historical results get "actual".
- Keep TAM, SAM and SOM separate. Check currencies, units and periods; do not convert currencies.
- Never assume an unknown value is zero. Never reconcile conflicting figures silently; report them.

Writing:
- thesis: one sentence of at most 35 words, built only on reported or calculated facts.
- Section summary: one or two short sentences (at most 30 words), reported facts only. It frames the
  section; do not repeat figures that appear in the section's metrics or points.
- Up to 3 metrics and 2 points per section; prefer the most decision-relevant numbers for this sector
  and stage. Metric labels are short (2-5 words); put qualifiers in period, geography or sizing_method.
- "missing" entries are short noun phrases (at most 8 words).
- numeric_value is in base units (1.2M -> 1200000, 38% -> 38). value is the short display form.
- key_metric_ids: 3-5 reported or calculated metric ids for the headline strip.
- analyst_notes: contradictions, unit or period problems, and other caveats you noticed.
- Team: only what the deck states. Use evidence "reported" with refs.
{research_rule}
Return ONLY a JSON object (no prose, no code fences) that validates against this JSON schema:
{schema}"""

EXIT_RULE_OFF = ("Exit scenarios were NOT requested: do not give exit valuations or investor returns. "
                 "Describe only the company's stated exit strategy (attribution 'company') and potential "
                 "acquirer categories (attribution 'analyst', evidence 'estimated').")
EXIT_RULE_ON = ("Exit scenarios WERE requested: you may add exit_value or investor_return metrics, "
                "evidence 'estimated', only with explicit assumptions (revenue at exit, multiple, dilution, "
                "timing). Keep company statements (attribution 'company') separate from analyst scenarios "
                "(attribution 'analyst').")
RESEARCH_OFF = "- Use only the deck. Do not use outside benchmarks or cite sources not in the deck."
RESEARCH_ON = (
    "- External research is enabled: you may use web_search for market sizes or comparables. For every "
    "external figure, add an external_sources entry {id, url, title} with the exact URL from a search "
    "result, and reference it in source_refs as the source id (e.g. 'EXT1'). Never invent citations, "
    "URLs or benchmarks. External figures are 'estimated' unless they are the deck's own claim."
)

CHUNK_PROMPT = """\
Below is part of a pitch deck. List every fact an investor would need: all numbers (with units,
currency and period), customers, pilots, contracts, team backgrounds, fundraise terms, use of funds,
market sizes, competition, IP and exit statements. Keep numbers exactly as written. Each fact must
carry the page/slide reference from the "=== ... ===" marker it came from.

Return ONLY JSON: {"facts": [{"ref": "Slide 4", "text": "..."}]}
"""


class _Fact(BaseModel):
    ref: str
    text: str


class _FactNotes(BaseModel):
    facts: list[_Fact]


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def analyze_deck(
    deck: DeckContent,
    settings: Settings | None = None,
    *,
    client=None,
    progress: Progress | None = None,
) -> Analysis:
    """Analyze an extracted deck and return a validated Analysis."""
    settings = settings or Settings()
    progress = progress or (lambda _msg: None)
    if client is None:
        client = _make_client()

    if not any(p.text.strip() for p in deck.pages):
        raise AnalysisError("The deck has no extractable text to analyze.")

    deck_text = deck.to_prompt_text()
    chunked = len(deck_text) > settings.max_chars_per_request
    if chunked:
        deck_text = _condense(deck, settings, client, progress)

    prompt = _analysis_prompt(settings) + "\n\n<deck>\n" + deck_text + "\n</deck>"
    progress(f"Analyzing with {settings.model}...")
    tools = [WEB_SEARCH_TOOL] if settings.external_research else None
    raw, search_urls = _call(client, settings, prompt, tools=tools)

    analysis = _parse_with_repair(raw, Analysis, client, settings, progress)
    if settings.external_research:
        _verify_sources(analysis, search_urls)
    else:
        analysis.external_sources = []

    analysis.meta = {
        "model": settings.model,
        "provider": "anthropic",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_file": deck.filename,
        "page_count": len(deck.pages),
        "chunked": chunked,
        "external_research": settings.external_research,
        "exit_scenarios_requested": settings.exit_scenarios,
        "demo": False,
    }
    return analysis


# --------------------------------------------------------------------------- #
# Prompt building and chunking
# --------------------------------------------------------------------------- #
def _analysis_prompt(settings: Settings) -> str:
    return RULES.format(
        exit_rule=EXIT_RULE_ON if settings.exit_scenarios else EXIT_RULE_OFF,
        research_rule=RESEARCH_ON if settings.external_research else RESEARCH_OFF,
        schema=json.dumps(llm_schema(), separators=(",", ":")),
    )


def chunk_pages(deck: DeckContent, max_chars: int) -> list[str]:
    """Group whole pages into chunks under max_chars, keeping their markers."""
    chunks, current, size = [], [], 0
    for p in deck.pages:
        block = f"=== {p.ref} ===\n{p.text or '[no extractable text]'}"
        if current and size + len(block) > max_chars:
            chunks.append("\n\n".join(current))
            current, size = [], 0
        current.append(block)
        size += len(block) + 2
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _condense(deck: DeckContent, settings: Settings, client, progress: Progress) -> str:
    chunks = chunk_pages(deck, max(settings.max_chars_per_request // 2, 1))
    lines: list[str] = []
    for i, chunk in enumerate(chunks, start=1):
        progress(f"Long deck: reading part {i} of {len(chunks)}...")
        raw, _ = _call(client, settings, CHUNK_PROMPT + "\n<deck_part>\n" + chunk + "\n</deck_part>")
        notes = _parse_with_repair(raw, _FactNotes, client, settings, progress)
        valid_refs = set(re.findall(r"=== (.+?) ===", chunk))
        for fact in notes.facts:
            ref = fact.ref if fact.ref in valid_refs else f"{fact.ref} (unverified ref)"
            lines.append(f"[{ref}] {fact.text}")
    # Re-group the notes under their original page markers.
    by_ref: dict[str, list[str]] = {}
    for line in lines:
        ref, _, text = line[1:].partition("] ")
        by_ref.setdefault(ref, []).append(text)
    return "\n\n".join(f"=== {ref} ===\n" + "\n".join(f"- {t}" for t in texts) for ref, texts in by_ref.items())


# --------------------------------------------------------------------------- #
# API call
# --------------------------------------------------------------------------- #
def _make_client():
    if not Settings.api_key_present():
        raise AnalysisError("ANTHROPIC_API_KEY is not set. Add it to your environment or .env, or use demo mode.")
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise AnalysisError("The 'anthropic' package is not installed.") from exc
    return anthropic.Anthropic()


def _call(client, settings: Settings, prompt: str, *, tools: list | None = None) -> tuple[str, set[str]]:
    """Run one request (continuing paused server-tool turns). Returns text and search-result URLs."""
    import anthropic

    request = dict(
        model=settings.model,
        max_tokens=settings.max_tokens,
        system=SYSTEM_PROMPT,
        output_config={"effort": settings.effort},
    )
    if tools:
        request["tools"] = tools
    if settings.model in FALLBACK_MODELS:
        request.update(betas=[FALLBACK_BETA], fallbacks="default")

    messages = [{"role": "user", "content": prompt}]
    texts: list[str] = []
    urls: set[str] = set()
    try:
        for _ in range(5):
            with client.beta.messages.stream(messages=messages, **request) as stream:
                response = stream.get_final_message()
            for block in response.content:
                btype = getattr(block, "type", "")
                if btype == "text":
                    texts.append(block.text)
                elif btype == "web_search_tool_result" and isinstance(block.content, list):
                    urls.update(getattr(r, "url", "") for r in block.content)
            if response.stop_reason != "pause_turn":
                break
            messages = messages + [{"role": "assistant", "content": response.content}]
    except anthropic.AuthenticationError as exc:
        raise AnalysisError("The Anthropic API key was rejected.") from exc
    except anthropic.RateLimitError as exc:
        raise AnalysisError("The Anthropic API rate limit was reached. Wait a minute and try again.") from exc
    except anthropic.APIStatusError as exc:
        raise AnalysisError(f"The Anthropic API returned an error ({exc.status_code}). Try again.") from exc
    except anthropic.APIConnectionError as exc:
        raise AnalysisError("Could not reach the Anthropic API. Check your network connection.") from exc

    if response.stop_reason == "refusal":
        raise AnalysisError("The model declined to analyze this content.")
    if response.stop_reason == "max_tokens":
        raise AnalysisError("The analysis was cut off before finishing. Try again or raise CLAUDE_MAX_TOKENS.")
    return "".join(texts), urls


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def extract_json(text: str) -> str:
    """Pull the JSON object out of a response that may carry fences or prose."""
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", t, re.S)
    if fence:
        return fence.group(1)
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found")
    return t[start:end + 1]


def _try_parse(raw: str, model: type[BaseModel]):
    try:
        return model.model_validate_json(extract_json(raw)), None
    except (ValueError, ValidationError) as exc:
        return None, exc


def _parse_with_repair(raw: str, model: type[BaseModel], client, settings: Settings, progress: Progress):
    parsed, err = _try_parse(raw, model)
    if parsed is not None:
        return parsed
    progress("The response did not match the schema; asking the model to repair it...")
    errors = _summarize_errors(err)
    repair = (
        "The JSON below does not validate. Fix it so it matches the schema, changing only what the errors "
        "require. Do not add facts. Return ONLY the corrected JSON object.\n\n"
        f"Errors:\n{errors}\n\nSchema:\n{json.dumps(model.model_json_schema(), separators=(',', ':'))}\n\n"
        f"JSON:\n{raw}"
    )
    raw2, _ = _call(client, settings, repair)
    parsed, err2 = _try_parse(raw2, model)
    if parsed is None:
        raise AnalysisError("The model returned output that could not be validated, even after a repair "
                            f"attempt ({_summarize_errors(err2, limit=3)}). Try again.")
    return parsed


def _summarize_errors(err: Exception | None, limit: int = 20) -> str:
    if isinstance(err, ValidationError):
        lines = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in err.errors()[:limit]]
        return "\n".join(lines)
    return str(err)


def _verify_sources(analysis: Analysis, search_urls: set[str]) -> None:
    """Keep only citations whose URL came back from an actual web search."""
    today = date.today().isoformat()
    kept, dropped = [], []
    for src in analysis.external_sources:
        if src.url in search_urls:
            src.accessed = today
            kept.append(src)
        else:
            dropped.append(src.id)
    analysis.external_sources = kept
    if not dropped:
        return
    analysis.analyst_notes.append(
        f"Removed unverifiable citations ({', '.join(dropped)}): their URLs were not in the search results."
    )
    # Items that relied on a removed citation become unapproved estimates.
    from .models import Evidence, iter_claims

    for _, item in iter_claims(analysis):
        if any(ref in dropped for ref in item.source_refs):
            item.source_refs = [r for r in item.source_refs if r not in dropped]
            if item.evidence != Evidence.NOT_PROVIDED:
                item.evidence = Evidence.ESTIMATED
                item.approved = False
                item.assumptions = list(item.assumptions) + ["Cited source could not be verified."]

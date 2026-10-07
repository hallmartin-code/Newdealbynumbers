"""TEN Capital Investor One-Pager Generator — Streamlit interface.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import math
import re
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st
from pydantic import ValidationError

from onepager.analysis import AnalysisError, analyze_deck
from onepager.config import ASSETS_DIR, PROVIDER_NAME, Settings
from onepager.demo import DEMO_DECK_PATH, load_demo_analysis
from onepager.evidence import pending_estimates, unresolved_conflicts
from onepager.export import export_json
from onepager.extraction import SUPPORTED_EXTENSIONS, ExtractionError, extract_deck, find_soffice, ocr_status
from onepager.models import SECTION_TITLES, Analysis, Evidence, MetricKind, Timeframe
from onepager.pdf import ContentOverflowError, render_pdf
from onepager.validation import ERROR, INFO, WARNING, parse_amount, parse_percent, validate

LOGO = ASSETS_DIR / "TEN_Capital_logo_footer.png"
EVIDENCE_OPTIONS = [e.value for e in Evidence]
TIMEFRAME_OPTIONS = [t.value for t in Timeframe]
KIND_OPTIONS = [k.value for k in MetricKind]

st.set_page_config(page_title="TEN Capital Investor One-Pager Generator", page_icon=str(LOGO), layout="wide")

ss = st.session_state
ss.setdefault("nonce", 0)       # bumps to reset the uploader
ss.setdefault("version", 0)     # bumps when a new analysis is loaded, resetting review widgets
ss.setdefault("deck", None)
ss.setdefault("base", None)     # analysis as produced (dict); review edits are applied on top
ss.setdefault("pdf", None)


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.image(str(LOGO), width=170)
    st.subheader("Settings")
    defaults = Settings()
    model = st.text_input("Model", value=defaults.model, help="Set CLAUDE_MODEL in .env to change the default.")
    can_ocr, ocr_msg = ocr_status()
    use_ocr = st.checkbox("OCR scanned PDF pages", value=defaults.ocr and can_ocr, disabled=not can_ocr)
    st.caption(ocr_msg)
    research = st.checkbox("External research (web search)", value=False,
                           help="Off by default. When on, the model may search the web; every source keeps "
                                "its URL and access date, and unverifiable citations are removed.")
    exit_scen = st.checkbox("Include exit valuation scenarios", value=False,
                            help="Off by default. When on, exit values or investor returns may be estimated, "
                                 "always with explicit assumptions and only after your approval.")
    st.divider()
    if Settings.api_key_present():
        st.success("API key found (ANTHROPIC_API_KEY).")
    else:
        st.warning("No ANTHROPIC_API_KEY set. Demo mode is available.")
    if st.button("Clear session", type="secondary", width="stretch",
                 help="Forget the uploaded deck, analysis and edits."):
        nonce = ss.nonce + 1
        ss.clear()
        ss.nonce = nonce
        st.rerun()

settings = Settings(model=model.strip() or defaults.model, ocr=use_ocr,
                    external_research=research, exit_scenarios=exit_scen)

st.title("TEN Capital Investor One-Pager Generator")
st.caption("Upload a pitch deck, review the extracted numbers and estimates, and export a one-page investor PDF.")


# --------------------------------------------------------------------------- #
# Step 1 — Upload and extract
# --------------------------------------------------------------------------- #
st.header("1. Upload deck")
c1, c2 = st.columns([3, 1])
with c1:
    upload = st.file_uploader("PDF, PPTX or PPT", type=[e.lstrip(".") for e in SUPPORTED_EXTENSIONS],
                              key=f"upload_{ss.nonce}")
    if upload and upload.name.lower().endswith(".ppt") and not find_soffice():
        st.warning("Legacy .ppt needs LibreOffice, which was not found. Save the file as .pptx (PowerPoint: "
                   "File > Save As > PowerPoint Presentation) or export it to PDF, then upload that.")
with c2:
    st.write("")
    use_sample = st.button("Use fictional sample deck", width="stretch")


def _extract(path: Path, name: str) -> None:
    try:
        ss.deck = extract_deck(path, ocr=settings.ocr, display_name=name)
        ss.base, ss.pdf = None, None
    except ExtractionError as exc:
        st.error(str(exc))


if upload and st.button("Extract text", type="primary"):
    # The upload is written to a private temp folder that is deleted right after extraction.
    with tempfile.TemporaryDirectory(prefix="onepager_") as tmp:
        path = Path(tmp) / ("upload" + Path(upload.name).suffix.lower())
        path.write_bytes(upload.getvalue())
        with st.spinner("Extracting text..."):
            _extract(path, upload.name)
if use_sample:
    _extract(DEMO_DECK_PATH, DEMO_DECK_PATH.name)

deck = ss.deck
if deck:
    st.success(f"**{deck.filename}**: {len(deck.pages)} {'slides' if deck.source_type != 'pdf' else 'pages'}, "
               f"{deck.char_count:,} characters extracted.")
    for w in deck.warnings:
        st.warning(w)
    with st.expander("Extracted text by page / slide"):
        for p in deck.pages:
            flag = " (OCR)" if p.ocr else (" (little text: possibly scanned)" if p.low_text else "")
            st.markdown(f"**{p.ref}**{flag}")
            st.text(p.text[:2000] or "[no text]")


# --------------------------------------------------------------------------- #
# Step 2 — Analyze
# --------------------------------------------------------------------------- #
st.header("2. Analyze")
has_key = Settings.api_key_present()
if deck:
    st.info(f"Analysis sends the extracted text ({deck.char_count:,} characters) to the configured external AI "
            f"provider, **{PROVIDER_NAME}**, using model **{settings.model}**"
            + (", with web search enabled" if settings.external_research else "")
            + ". Nothing is sent until you click Analyze. Uploaded files are deleted after extraction, and deck "
              "contents and credentials are not logged.")
    consent = st.checkbox("I understand and want to send this deck text for analysis.")
    if st.button("Analyze deck", type="primary", disabled=not (has_key and consent)):
        with st.status("Analyzing deck...", expanded=True) as status:
            try:
                analysis = analyze_deck(deck, settings, progress=status.write)
                ss.base, ss.pdf = analysis.model_dump(mode="json"), None
                ss.version += 1
                status.update(label="Analysis complete", state="complete")
            except AnalysisError as exc:
                status.update(label="Analysis failed", state="error")
                st.error(str(exc))
    if not has_key:
        st.caption("Set ANTHROPIC_API_KEY to enable analysis, or load the demo analysis below.")
else:
    st.caption("Upload and extract a deck first.")

if st.button("Load demo analysis (fictional sample data)", disabled=False):
    ss.base, ss.pdf = load_demo_analysis().model_dump(mode="json"), None
    ss.version += 1


# --------------------------------------------------------------------------- #
# Step 3 — Review
# --------------------------------------------------------------------------- #
def _clean(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


def _refs(v) -> list[str]:
    return [r.strip() for r in str(_clean(v) or "").split(",") if r.strip()]


def _numeric(value: str | None):
    if not value:
        return None
    return parse_percent(value) if "%" in value else parse_amount(value)[0]


def _editor(rows: list[dict], cols: list[str], key: str, config: dict, hidden=("_i",), dynamic=True):
    df = pd.DataFrame(rows, columns=cols + list(hidden))
    edited = st.data_editor(
        df, key=key, hide_index=True, width="stretch",
        num_rows="dynamic" if dynamic else "fixed",
        column_order=cols, column_config=config,
    )
    return edited.to_dict("records")


def review_section(key: str, base_sec: dict, v: int) -> dict:
    sec = dict(base_sec)
    sec["summary"] = st.text_area("Summary", base_sec.get("summary", ""), key=f"sum_{key}_{v}", height=80)

    # Metrics
    base_metrics = {m["id"]: m for m in base_sec.get("metrics", [])}
    rows = [{"id": m["id"], "label": m["label"], "value": m.get("value"), "kind": m.get("kind"),
             "evidence": m["evidence"], "timeframe": m.get("timeframe"), "period": m.get("period"),
             "refs": ", ".join(m.get("source_refs", [])), "approved": m.get("approved", False)}
            for m in base_sec.get("metrics", [])]
    st.markdown("**Metrics**")
    edited = _editor(rows, ["id", "label", "value", "kind", "evidence", "timeframe", "period", "refs", "approved"],
                     f"met_{key}_{v}", {
                         "id": st.column_config.TextColumn("id", help="Unique id; used for key metrics and formulas."),
                         "kind": st.column_config.SelectboxColumn("kind", options=KIND_OPTIONS),
                         "evidence": st.column_config.SelectboxColumn("evidence", options=EVIDENCE_OPTIONS, required=True),
                         "timeframe": st.column_config.SelectboxColumn("timeframe", options=TIMEFRAME_OPTIONS),
                         "approved": st.column_config.CheckboxColumn("approve estimate"),
                     }, hidden=())
    metrics = []
    for i, r in enumerate(edited):
        mid = _clean(r.get("id")) or f"{key}_metric_{i + 1}"
        base = dict(base_metrics.get(mid, {}))
        value = _clean(r.get("value"))
        numeric = base.get("numeric_value") if value == base.get("value") else _numeric(value)
        metrics.append({**base, "id": mid, "label": _clean(r.get("label")) or mid, "value": value,
                        "numeric_value": numeric, "kind": _clean(r.get("kind")) or "other",
                        "evidence": _clean(r.get("evidence")) or "reported",
                        "timeframe": _clean(r.get("timeframe")) or "n/a", "period": _clean(r.get("period")),
                        "source_refs": _refs(r.get("refs")), "approved": bool(r.get("approved"))})
    sec["metrics"] = metrics

    # Points
    base_points = base_sec.get("points", [])
    rows = [{"text": p["text"], "evidence": p["evidence"], "attribution": p.get("attribution", "company"),
             "timeframe": p.get("timeframe", "n/a"), "refs": ", ".join(p.get("source_refs", [])),
             "approved": p.get("approved", False), "_i": i} for i, p in enumerate(base_points)]
    st.markdown("**Points**")
    sec["points"] = _points_from(_editor(rows, ["text", "evidence", "attribution", "timeframe", "refs", "approved"],
                                         f"pts_{key}_{v}", _point_config()), base_points)

    if key == "team":
        st.markdown("**Team members**")
        rows = [{"name": m["name"], "role": m["role"], "background": m.get("background", ""),
                 "refs": ", ".join(m.get("source_refs", []))} for m in base_sec.get("members", [])]
        ed = _editor(rows, ["name", "role", "background", "refs"], f"mem_{key}_{v}", {}, hidden=())
        sec["members"] = [{"name": _clean(r.get("name")) or "", "role": _clean(r.get("role")) or "",
                           "background": _clean(r.get("background")) or "", "source_refs": _refs(r.get("refs"))}
                          for r in ed if _clean(r.get("name"))]

    if key == "use_of_funds":
        base_allocs = base_sec.get("allocations", [])
        rows = [{"category": a["category"], "percent": a.get("percent"), "amount": a.get("amount"),
                 "evidence": a["evidence"], "proposed": a.get("proposed", False),
                 "refs": ", ".join(a.get("source_refs", [])), "approved": a.get("approved", False), "_i": i}
                for i, a in enumerate(base_allocs)]
        st.markdown("**Allocations**")
        ed = _editor(rows, ["category", "percent", "amount", "evidence", "proposed", "refs", "approved"],
                     f"alloc_{key}_{v}", {
                         "percent": st.column_config.NumberColumn("percent", min_value=0, max_value=100),
                         "evidence": st.column_config.SelectboxColumn("evidence", options=EVIDENCE_OPTIONS, required=True),
                         "approved": st.column_config.CheckboxColumn("approve estimate"),
                     })
        allocs = []
        for r in ed:
            if not _clean(r.get("category")):
                continue
            base = dict(base_allocs[int(r["_i"])]) if _clean(r.get("_i")) is not None else {}
            amount = _clean(r.get("amount"))
            allocs.append({**base, "category": r["category"], "percent": _clean(r.get("percent")), "amount": amount,
                           "amount_value": base.get("amount_value") if amount == base.get("amount") else parse_amount(amount)[0],
                           "evidence": _clean(r.get("evidence")) or "reported", "proposed": bool(r.get("proposed")),
                           "source_refs": _refs(r.get("refs")), "approved": bool(r.get("approved"))})
        sec["allocations"] = allocs
        base_ms = base_sec.get("milestones", [])
        rows = [{"text": p["text"], "evidence": p["evidence"], "attribution": p.get("attribution", "company"),
                 "timeframe": p.get("timeframe", "n/a"), "refs": ", ".join(p.get("source_refs", [])),
                 "approved": p.get("approved", False), "_i": i} for i, p in enumerate(base_ms)]
        st.markdown("**Milestones**")
        sec["milestones"] = _points_from(_editor(rows, ["text", "evidence", "attribution", "timeframe", "refs", "approved"],
                                                 f"ms_{key}_{v}", _point_config()), base_ms)

    missing = st.text_area("Not provided (one per line)", "\n".join(base_sec.get("missing", [])),
                           key=f"miss_{key}_{v}", height=68)
    sec["missing"] = [ln.strip() for ln in missing.splitlines() if ln.strip()]
    _show_details(key, sec)
    return sec


def _point_config() -> dict:
    return {
        "text": st.column_config.TextColumn("text", width="large"),
        "evidence": st.column_config.SelectboxColumn("evidence", options=EVIDENCE_OPTIONS, required=True),
        "attribution": st.column_config.SelectboxColumn("attribution", options=["company", "analyst"]),
        "timeframe": st.column_config.SelectboxColumn("timeframe", options=TIMEFRAME_OPTIONS),
        "approved": st.column_config.CheckboxColumn("approve estimate"),
    }


def _points_from(rows: list[dict], base_points: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        text = _clean(r.get("text"))
        if not text:
            continue
        idx = _clean(r.get("_i"))
        base = dict(base_points[int(idx)]) if idx is not None else {}
        out.append({**base, "text": text, "evidence": _clean(r.get("evidence")) or "reported",
                    "attribution": _clean(r.get("attribution")) or "company",
                    "timeframe": _clean(r.get("timeframe")) or "n/a",
                    "source_refs": _refs(r.get("refs")), "approved": bool(r.get("approved"))})
    return out


def _show_details(key: str, sec: dict) -> None:
    """Assumptions behind estimates, formulas, and conflicting values."""
    lines = []
    items = sec.get("metrics", []) + sec.get("points", []) + sec.get("allocations", []) + sec.get("milestones", [])
    for it in items:
        name = it.get("label") or it.get("category") or (it.get("text", "")[:60] + "...")
        ev = it.get("evidence")
        if ev == "estimated":
            bits = []
            if it.get("method"):
                bits.append(f"method: {it['method']}")
            if it.get("assumptions"):
                bits.append("assumptions: " + "; ".join(it["assumptions"]))
            if it.get("range_low") and it.get("range_high"):
                bits.append(f"range {it['range_low']} to {it['range_high']}")
            if it.get("confidence"):
                bits.append(f"confidence: {it['confidence']}")
            state = "approved" if it.get("approved") else "awaiting approval"
            lines.append(f"- **Estimate ({state}) — {name}**: " + ("; ".join(bits) or "no assumptions given"))
        elif ev == "calculated" and it.get("formula"):
            lines.append(f"- **Calculated — {name}**: {it['formula']}")
        elif ev == "conflicting":
            vals = "; ".join(f"{c['value']} ({c['source_ref']})" for c in it.get("conflicting_values", []))
            lines.append(f"- **Conflict — {name}**: {vals or 'values not listed'}. To resolve, enter the correct "
                         "value and set evidence to reported (with its slide).")
    if lines:
        st.markdown("\n".join(lines))


if ss.base:
    st.header("3. Review")
    base = ss.base
    v = ss.version
    if base.get("meta", {}).get("demo"):
        st.warning("DEMO MODE: fictional sample data for the fictional Northpeak Cold Chain deck. "
                   "This is not an AI analysis of your upload.")

    current = dict(base)
    current["company_name"] = st.text_input("Company name", base["company_name"], key=f"name_{v}")
    current["thesis"] = st.text_area("One-sentence investment thesis", base["thesis"], key=f"thesis_{v}", height=70)

    sections = dict(base["sections"])
    for key, title in SECTION_TITLES.items():
        with st.expander(title, expanded=False):
            sections[key] = review_section(key, base["sections"][key], v)
    current["sections"] = sections

    metric_ids = [m["id"] for s in sections.values() for m in s.get("metrics", [])]
    current["key_metric_ids"] = st.multiselect(
        "Headline metrics (3-5)", metric_ids, default=[i for i in base.get("key_metric_ids", []) if i in metric_ids],
        max_selections=5, key=f"keys_{v}")

    try:
        analysis = Analysis.model_validate(current)
    except ValidationError as exc:
        st.error("Some edits are not valid; fix them to continue:\n\n" +
                 "\n".join(f"- {'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:10]))
        st.stop()

    issues = validate(analysis)
    pending = pending_estimates(analysis)
    conflicts = unresolved_conflicts(analysis)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Errors", sum(i.severity == ERROR for i in issues))
    m2.metric("Warnings", sum(i.severity == WARNING for i in issues))
    m3.metric("Estimates awaiting approval", len(pending))
    m4.metric("Unresolved conflicts", len(conflicts))

    with st.expander("Validation findings", expanded=any(i.severity == ERROR for i in issues)):
        if not issues:
            st.write("No issues found.")
        icon = {ERROR: "🔴", WARNING: "🟠", INFO: "🔵"}
        for i in issues:
            target = SECTION_TITLES.get(i.section, i.section.title())
            st.markdown(f"{icon[i.severity]} **{target}**{f' · `{i.item_id}`' if i.item_id else ''}: {i.message}")
    if pending:
        st.info("Estimates stay out of the PDF until approved. Tick **approve estimate** in a section's table "
                "after checking its assumptions.")

    # ----------------------------------------------------------------------- #
    # Step 4 — Export
    # ----------------------------------------------------------------------- #
    st.header("4. Export")
    fingerprint = analysis.model_dump_json()
    if ss.pdf and ss.pdf.get("fingerprint") != fingerprint:
        st.warning("You have edited the analysis since the last PDF. Generate it again to include the changes.")
        ss.pdf = None
    if st.button("Generate one-page PDF", type="primary"):
        try:
            result = render_pdf(analysis)
            ss.pdf = {"bytes": result.pdf, "trim_level": result.trim_level, "excluded": result.excluded,
                      "font": result.font, "fingerprint": fingerprint}
        except ContentOverflowError as exc:
            ss.pdf = None
            st.error(str(exc))
            st.markdown("\n".join(f"- {t}: {h:.0f} pt" for t, h in exc.sections))

    if ss.pdf:
        info = ss.pdf
        ex = info["excluded"]
        note = []
        if info["trim_level"]:
            note.append(f"content shortened to fit (level {info['trim_level']})")
        if ex.get("unapproved_estimates"):
            note.append(f"{ex['unapproved_estimates']} unapproved estimate(s) left out")
        if ex.get("conflicting"):
            note.append(f"{ex['conflicting']} conflicting figure(s) left out")
        st.success("PDF ready: exactly one page" + (f"; {', '.join(note)}." if note else "."))
        try:
            import pymupdf

            with pymupdf.open(stream=info["bytes"], filetype="pdf") as doc:
                st.image(doc[0].get_pixmap(dpi=110).tobytes("png"), caption="Preview", width=650)
        except Exception:  # noqa: BLE001 - preview is optional
            pass
        slug = re.sub(r"[^A-Za-z0-9]+", "_", analysis.company_name).strip("_") or "company"
        pdf_info = {"trim_level": info["trim_level"], "excluded": info["excluded"], "font": info["font"], "pages": 1}
        d1, d2 = st.columns(2)
        d1.download_button("Download PDF", info["bytes"], f"{slug}_one_pager.pdf", "application/pdf",
                           width="stretch")
        d2.download_button("Download analysis JSON", export_json(analysis, issues, pdf_info),
                           f"{slug}_analysis.json", "application/json", width="stretch")
    else:
        st.download_button("Download analysis JSON", export_json(analysis, issues),
                           "analysis.json", "application/json")

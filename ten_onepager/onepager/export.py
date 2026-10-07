"""Structured JSON export of the reviewed analysis."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone

from . import __version__
from .evidence import pdf_eligible
from .models import Analysis, Evidence, iter_claims
from .validation import Issue


def export_payload(analysis: Analysis, issues: list[Issue], pdf_info: dict | None = None) -> dict:
    """Everything behind the one-pager: the analysis (with approvals and all
    estimate assumptions), validation findings, and what the PDF included."""
    exit_ok = bool(analysis.meta.get("exit_scenarios_requested"))
    approved, excluded = [], []
    for key, item in iter_claims(analysis):
        ident = getattr(item, "id", None) or getattr(item, "category", None) or item.text[:60]
        if item.evidence == Evidence.ESTIMATED:
            (approved if pdf_eligible(key, item, exit_scenarios=exit_ok) else excluded).append(
                {"section": key, "item": ident, "assumptions": item.assumptions}
            )
    return {
        "generator": "TEN Capital Investor One-Pager Generator",
        "version": __version__,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "analysis": analysis.model_dump(mode="json"),
        "estimates": {"approved_for_pdf": approved, "not_in_pdf": excluded},
        "validation_issues": [asdict(i) for i in issues],
        "pdf": pdf_info or {},
    }


def export_json(analysis: Analysis, issues: list[Issue], pdf_info: dict | None = None) -> str:
    return json.dumps(export_payload(analysis, issues, pdf_info), indent=2, ensure_ascii=False)

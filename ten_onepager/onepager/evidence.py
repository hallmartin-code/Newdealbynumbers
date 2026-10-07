"""Evidence rules: which claims may appear in the exported PDF.

- Reported and calculated claims are eligible.
- Estimated claims are eligible only after the user approves them, and never
  for quantities that must not be estimated (revenue, customers, commitments,
  investment terms, regulatory status, founder credentials).
- Conflicting claims stay out until the user resolves them in review.
- Not-provided claims are rendered as gaps, not as figures.
- Exit valuations and investor returns appear only when the user asked for
  exit scenarios.
"""

from __future__ import annotations

from .models import Allocation, Analysis, Claim, Evidence, Metric, MetricKind, iter_claims

# Quantities that must never be estimated, only reported or calculated.
NO_ESTIMATE_KINDS = {
    MetricKind.REVENUE,
    MetricKind.ARR,
    MetricKind.MRR,
    MetricKind.BOOKINGS,
    MetricKind.GMV,
    MetricKind.PAYING_CUSTOMERS,
    MetricKind.PILOTS,
    MetricKind.USERS,
    MetricKind.RAISE_TARGET,
    MetricKind.PRE_MONEY,
    MetricKind.POST_MONEY,
    MetricKind.VALUATION_CAP,
    MetricKind.CASH_RECEIVED,
    MetricKind.COMMITMENT_SIGNED,
    MetricKind.COMMITMENT_VERBAL,
    MetricKind.REGULATORY,
    MetricKind.TEAM,
}

EXIT_SCENARIO_KINDS = {MetricKind.EXIT_VALUE, MetricKind.INVESTOR_RETURN}


def is_prohibited_estimate(section_key: str, item: Claim) -> bool:
    """True when an estimate covers something that must not be estimated."""
    if item.evidence != Evidence.ESTIMATED:
        return False
    if section_key == "team":
        return True
    return isinstance(item, Metric) and item.kind in NO_ESTIMATE_KINDS


def needs_approval(section_key: str, item: Claim) -> bool:
    return item.evidence == Evidence.ESTIMATED and not is_prohibited_estimate(section_key, item)


def pdf_eligible(section_key: str, item: Claim, *, exit_scenarios: bool = False) -> bool:
    """Whether a claim may be printed as a figure or statement in the PDF."""
    if isinstance(item, Metric) and item.kind in EXIT_SCENARIO_KINDS and not exit_scenarios:
        return False
    if isinstance(item, Metric) and item.evidence != Evidence.NOT_PROVIDED and not item.value:
        return False
    if item.evidence in (Evidence.REPORTED, Evidence.CALCULATED):
        return True
    if item.evidence == Evidence.ESTIMATED:
        return item.approved and not is_prohibited_estimate(section_key, item)
    return False


def pending_estimates(analysis: Analysis) -> list[tuple[str, Claim]]:
    """Estimates awaiting a decision in the review screen."""
    return [
        (k, c) for k, c in iter_claims(analysis)
        if needs_approval(k, c) and not c.approved
    ]


def unresolved_conflicts(analysis: Analysis) -> list[tuple[str, Claim]]:
    return [(k, c) for k, c in iter_claims(analysis) if c.evidence == Evidence.CONFLICTING]


def label_for(item: Claim) -> str:
    """Short visible tag for the PDF: EST., FORECAST, PROPOSED or CALC."""
    tags = []
    if item.evidence == Evidence.ESTIMATED:
        tags.append("EST.")
    if getattr(item, "timeframe", None) and item.timeframe.value == "forecast":
        tags.append("FORECAST")
    if isinstance(item, (Metric, Allocation)) and item.proposed:
        tags.append("PROPOSED")
    if item.evidence == Evidence.CALCULATED:
        tags.append("CALC.")
    return " · ".join(tags)

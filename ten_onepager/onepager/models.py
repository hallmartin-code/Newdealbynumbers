"""Pydantic schema for the deck analysis.

Every figure and statement carries an evidence label so the review screen,
validator and PDF renderer can treat reported facts, calculations, estimates,
gaps and contradictions differently. Fields marked APP-ONLY are set by the
application (for example, user approval) and are not requested from the model.
"""

from __future__ import annotations

from enum import Enum
from typing import Iterator, Literal

from pydantic import BaseModel, ConfigDict, Field


class Evidence(str, Enum):
    REPORTED = "reported"          # stated in the deck, with a page/slide reference
    CALCULATED = "calculated"      # derived from reported figures, formula preserved
    ESTIMATED = "estimated"        # based on disclosed assumptions, needs approval
    NOT_PROVIDED = "not_provided"  # missing and not responsibly estimable
    CONFLICTING = "conflicting"    # inconsistent across the deck, both values kept


class Timeframe(str, Enum):
    ACTUAL = "actual"
    FORECAST = "forecast"
    NOT_APPLICABLE = "n/a"


class MetricKind(str, Enum):
    """What a figure measures. Used to keep look-alike metrics apart."""

    REVENUE = "revenue"
    ARR = "arr"
    MRR = "mrr"
    BOOKINGS = "bookings"
    GMV = "gmv"
    PIPELINE = "pipeline"
    PAYING_CUSTOMERS = "paying_customers"
    PILOTS = "pilots"
    USERS = "users"
    GROWTH_RATE = "growth_rate"
    RETENTION = "retention"
    GROSS_MARGIN = "gross_margin"
    CUSTOMER_BENEFIT = "customer_benefit"
    PROBLEM_COST = "problem_cost"
    PROBLEM_SCALE = "problem_scale"
    TAM = "tam"
    SAM = "sam"
    SOM = "som"
    RAISE_TARGET = "raise_target"
    PRE_MONEY = "pre_money_valuation"
    POST_MONEY = "post_money_valuation"
    VALUATION_CAP = "valuation_cap"
    CASH_RECEIVED = "cash_received"
    COMMITMENT_SIGNED = "commitment_signed"
    COMMITMENT_VERBAL = "commitment_verbal"
    RAISE_REMAINING = "raise_remaining"
    RUNWAY = "runway"
    BURN = "burn"
    REGULATORY = "regulatory"
    IP = "ip"
    TEAM = "team"
    EXIT_VALUE = "exit_value"
    INVESTOR_RETURN = "investor_return"
    OTHER = "other"


class Confidence(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore", use_enum_values=False)


class ConflictingValue(_Base):
    value: str
    source_ref: str


class Metric(_Base):
    id: str = Field(description="Unique, short, snake_case id, e.g. 'arr_current'.")
    label: str = Field(description="What the figure is, e.g. 'ARR'.")
    value: str | None = Field(None, description="Display value, e.g. '$1.2M'. Null when not provided.")
    numeric_value: float | None = Field(None, description="Value in base units (1.2M -> 1200000; 38% -> 38).")
    unit: str | None = Field(None, description="Currency code (USD, EUR), '%', 'customers', 'months', 'x', ...")
    period: str | None = Field(None, description="Reporting period or as-of date, e.g. 'June 2026', 'FY2025'.")
    kind: MetricKind = MetricKind.OTHER
    timeframe: Timeframe = Timeframe.NOT_APPLICABLE
    evidence: Evidence
    source_refs: list[str] = Field(default_factory=list, description="e.g. ['Slide 4'] or ['Page 2'].")
    # Calculated
    formula: str | None = Field(None, description="Human-readable formula with inputs, e.g. 'ARR / paying customers = $1.2M / 14'.")
    expression: str | None = Field(None, description="Machine-checkable arithmetic over metric ids, e.g. 'arr_current / paying_customers'.")
    inputs: list[str] = Field(default_factory=list, description="Ids of metrics used as inputs.")
    # Estimated
    method: str | None = None
    assumptions: list[str] = Field(default_factory=list)
    range_low: str | None = None
    range_high: str | None = None
    confidence: Confidence | None = None
    # Market sizing
    geography: str | None = None
    year: str | None = None
    sizing_method: str | None = Field(None, description="top-down, bottom-up, third-party report, ...")
    # Conflicting
    conflicting_values: list[ConflictingValue] = Field(default_factory=list)
    proposed: bool = Field(False, description="True for hypothetical allocations or plans.")
    approved: bool = False  # APP-ONLY


class Point(_Base):
    text: str
    evidence: Evidence
    source_refs: list[str] = Field(default_factory=list)
    timeframe: Timeframe = Timeframe.NOT_APPLICABLE
    attribution: Literal["company", "analyst"] = "company"
    assumptions: list[str] = Field(default_factory=list)
    approved: bool = False  # APP-ONLY


class TeamMember(_Base):
    name: str
    role: str
    background: str = Field("", description="Relevant experience and prior outcomes, as stated in the deck.")
    source_refs: list[str] = Field(default_factory=list)


class Allocation(_Base):
    category: str
    percent: float | None = None
    amount: str | None = None
    amount_value: float | None = None
    evidence: Evidence
    source_refs: list[str] = Field(default_factory=list)
    proposed: bool = False
    assumptions: list[str] = Field(default_factory=list)
    approved: bool = False  # APP-ONLY


class Section(_Base):
    summary: str = Field("", description="1-2 sentences, investor-relevant, reported facts only.")
    metrics: list[Metric] = Field(default_factory=list)
    points: list[Point] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list, description="Information an investor needs that the deck does not provide.")


class TeamSection(Section):
    members: list[TeamMember] = Field(default_factory=list)


class UseOfFundsSection(Section):
    allocations: list[Allocation] = Field(default_factory=list)
    milestones: list[Point] = Field(default_factory=list)


class Sections(_Base):
    problem: Section
    solution: Section
    team: TeamSection
    traction: Section
    market_size: Section
    competitive_advantage: Section
    fundraise: Section
    use_of_funds: UseOfFundsSection
    exit: Section


class ExternalSource(_Base):
    id: str
    url: str
    title: str = ""
    accessed: str = ""


class Analysis(_Base):
    company_name: str
    thesis: str = Field(description="One sentence investment thesis built only on reported or calculated facts.")
    sector: str | None = None
    stage: str | None = None
    reporting_currency: str | None = None
    deck_date: str | None = None
    key_metric_ids: list[str] = Field(default_factory=list, description="3-5 metric ids for the headline strip.")
    sections: Sections
    analyst_notes: list[str] = Field(default_factory=list, description="Contradictions, unit problems or caveats noticed.")
    external_sources: list[ExternalSource] = Field(default_factory=list)
    meta: dict = Field(default_factory=dict)  # APP-ONLY


SECTION_TITLES: dict[str, str] = {
    "problem": "Problem",
    "solution": "Solution",
    "team": "Team",
    "traction": "Traction",
    "market_size": "Market Size",
    "competitive_advantage": "Competitive Advantage",
    "fundraise": "Fundraise",
    "use_of_funds": "Use of Funds",
    "exit": "Exit",
}

APP_ONLY_FIELDS = {"approved", "meta"}

Claim = Metric | Point | Allocation


def iter_sections(analysis: Analysis) -> Iterator[tuple[str, Section]]:
    for key in SECTION_TITLES:
        yield key, getattr(analysis.sections, key)


def iter_claims(analysis: Analysis) -> Iterator[tuple[str, Claim]]:
    """Yield (section_key, item) for every labelled metric, point and allocation."""
    for key, section in iter_sections(analysis):
        for m in section.metrics:
            yield key, m
        for p in section.points:
            yield key, p
        if isinstance(section, UseOfFundsSection):
            for a in section.allocations:
                yield key, a
            for p in section.milestones:
                yield key, p


def all_metrics(analysis: Analysis) -> dict[str, Metric]:
    return {m.id: m for _, s in iter_sections(analysis) for m in s.metrics}


def llm_schema() -> dict:
    """JSON schema for the model's output, without application-only fields."""
    schema = Analysis.model_json_schema()

    def strip(node):
        if isinstance(node, dict):
            props = node.get("properties")
            if isinstance(props, dict):
                for f in APP_ONLY_FIELDS:
                    props.pop(f, None)
                if "required" in node:
                    node["required"] = [r for r in node["required"] if r not in APP_ONLY_FIELDS]
            for v in node.values():
                strip(v)
        elif isinstance(node, list):
            for v in node:
                strip(v)

    strip(schema)
    return schema

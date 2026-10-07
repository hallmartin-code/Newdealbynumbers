"""Deterministic financial validation of an analysis.

Checks currencies, units, periods, percentages and arithmetic, and flags
look-alike metrics that have been mixed up (pilots vs paying customers, ARR vs
revenue, commitments vs cash, forecast vs actual, TAM vs SAM vs SOM). Nothing
here changes the analysis: every finding becomes an Issue for the review
screen, and conflicting or unknown values are never reconciled or zero-filled.
"""

from __future__ import annotations

import ast
import operator
import re
from dataclasses import dataclass
from datetime import date

from .evidence import is_prohibited_estimate
from .models import (
    Analysis,
    Evidence,
    Metric,
    MetricKind,
    Point,
    Timeframe,
    UseOfFundsSection,
    all_metrics,
    iter_claims,
)

ERROR, WARNING, INFO = "error", "warning", "info"
_SEVERITY_ORDER = {ERROR: 0, WARNING: 1, INFO: 2}


@dataclass(frozen=True)
class Issue:
    severity: str
    section: str
    message: str
    item_id: str | None = None


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #
_CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "₹": "INR"}
_CURRENCY_CODES = {"USD", "EUR", "GBP", "JPY", "INR", "CAD", "AUD", "CHF", "SGD", "CNY"}
_MULTIPLIERS = {
    "k": 1e3, "thousand": 1e3,
    "m": 1e6, "mm": 1e6, "mn": 1e6, "million": 1e6,
    "b": 1e9, "bn": 1e9, "billion": 1e9,
    "t": 1e12, "tn": 1e12, "trillion": 1e12,
}
_AMOUNT_RE = re.compile(
    r"(?P<num>\d[\d,]*(?:\.\d+)?|\.\d+)\s*(?P<mult>thousand|million|billion|trillion|mm|mn|bn|tn|k|m|b|t)?\b",
    re.IGNORECASE,
)


def parse_amount(text: str | None) -> tuple[float | None, str | None]:
    """Parse '$4.2M', '€1.5bn', 'USD 850K', '1,200,000' -> (value, currency).

    Returns (None, None) when no number is found. Ranges such as '$1-2M' are
    treated as unparseable rather than silently picking one end.
    """
    if not text:
        return None, None
    s = text.strip()
    if re.search(r"\d\s*(?:-|–|to)\s*\$?\d", s):
        return None, _currency_of(s)
    m = _AMOUNT_RE.search(s.replace("~", "").replace("≈", ""))
    if not m:
        return None, _currency_of(s)
    value = float(m.group("num").replace(",", ""))
    mult = (m.group("mult") or "").lower()
    value *= _MULTIPLIERS.get(mult, 1.0)
    return value, _currency_of(s)


def _currency_of(s: str) -> str | None:
    for sym, code in _CURRENCY_SYMBOLS.items():
        if sym in s:
            return code
    for code in _CURRENCY_CODES:
        if re.search(rf"\b{code}\b", s.upper()):
            return code
    return None


def parse_percent(text: str | None) -> float | None:
    if not text:
        return None
    if re.search(r"\d\s*%?\s*(?:-|–|to)\s*\d", text):  # ranges such as 4-6% are not one number
        return None
    m = re.search(r"(?<![\d.])(-?\d+(?:\.\d+)?)\s*%", text)
    return float(m.group(1)) if m else None


def metric_number(m: Metric) -> float | None:
    """Best numeric reading of a metric: numeric_value first, else parse value."""
    if m.numeric_value is not None:
        return m.numeric_value
    if m.unit == "%" or (m.value and "%" in m.value):
        return parse_percent(m.value)
    return parse_amount(m.value)[0]


def metric_currency(m: Metric) -> str | None:
    if m.unit and m.unit.upper() in _CURRENCY_CODES:
        return m.unit.upper()
    return _currency_of(m.value or "")


def close(a: float, b: float, rel: float = 0.02, abs_tol: float = 0.5) -> bool:
    return abs(a - b) <= max(abs_tol, rel * max(abs(a), abs(b)))


# Safe arithmetic evaluator for calculated-metric expressions.
_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.USub: operator.neg, ast.UAdd: operator.pos,
}


def evaluate_expression(expr: str, values: dict[str, float]) -> float:
    """Evaluate '+ - * /' arithmetic over metric ids and numeric literals."""
    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.operand))
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in values:
                raise KeyError(node.id)
            return values[node.id]
        raise ValueError("unsupported expression")
    return ev(ast.parse(expr, mode="eval"))


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #
def validate(analysis: Analysis, *, today: date | None = None) -> list[Issue]:
    today = today or date.today()
    issues: list[Issue] = []
    metrics = all_metrics(analysis)
    section_of = {m.id: k for k, s in _sections(analysis) for m in s.metrics}

    issues += _check_ids(analysis)
    issues += _check_evidence(analysis)
    issues += _check_categories(analysis)
    issues += _check_timeframes(analysis, today)
    issues += _check_units_and_currency(analysis, metrics, section_of)
    issues += _check_calculations(metrics, section_of)
    issues += _check_market(analysis, metrics, section_of)
    issues += _check_fundraise(metrics, section_of)
    issues += _check_use_of_funds(analysis, metrics)
    for note in analysis.analyst_notes:
        issues.append(Issue(INFO, "general", f"Analyst note: {note}"))
    for mid in analysis.key_metric_ids:
        if mid not in metrics:
            issues.append(Issue(WARNING, "general", f"Key metric '{mid}' does not exist.", mid))
    return sorted(issues, key=lambda i: (_SEVERITY_ORDER[i.severity], i.section))


def _sections(analysis: Analysis):
    from .models import iter_sections
    return iter_sections(analysis)


def _item_id(item) -> str | None:
    return getattr(item, "id", None) or getattr(item, "category", None) or (
        item.text[:40] if isinstance(item, Point) else None
    )


def _check_ids(analysis: Analysis) -> list[Issue]:
    seen, out = set(), []
    for key, s in _sections(analysis):
        for m in s.metrics:
            if m.id in seen:
                out.append(Issue(ERROR, key, f"Duplicate metric id '{m.id}'.", m.id))
            seen.add(m.id)
    return out


def _check_evidence(analysis: Analysis) -> list[Issue]:
    out = []
    for key, item in iter_claims(analysis):
        iid = _item_id(item)
        ev = item.evidence
        if ev == Evidence.REPORTED and not item.source_refs:
            out.append(Issue(WARNING, key, "Reported item has no page/slide reference.", iid))
        if ev == Evidence.CALCULATED and isinstance(item, Metric) and not (item.formula or item.expression):
            out.append(Issue(WARNING, key, "Calculated figure has no formula or inputs.", iid))
        if ev == Evidence.ESTIMATED and not item.assumptions and not getattr(item, "method", None):
            out.append(Issue(WARNING, key, "Estimate has no stated method or assumptions.", iid))
        if ev == Evidence.ESTIMATED and isinstance(item, Metric) and item.confidence is None:
            out.append(Issue(INFO, key, "Estimate has no confidence level.", iid))
        if ev == Evidence.CONFLICTING:
            if isinstance(item, Metric) and len(item.conflicting_values) < 2:
                out.append(Issue(WARNING, key, "Conflicting figure should list both values and sources.", iid))
            out.append(Issue(WARNING, key, "Conflicting values: resolve in review before it can appear in the PDF.", iid))
        if ev == Evidence.NOT_PROVIDED and isinstance(item, Metric) and item.numeric_value not in (None,):
            out.append(Issue(WARNING, key, "Not-provided item carries a number; it will not be shown.", iid))
        if is_prohibited_estimate(key, item):
            out.append(Issue(ERROR, key, "This must not be estimated (revenue, customers, commitments, terms, "
                                         "regulatory status or team). It is excluded from the PDF.", iid))
        if ev == Evidence.ESTIMATED and isinstance(item, Metric) and item.numeric_value == 0:
            out.append(Issue(WARNING, key, "Estimate of zero: confirm this is not an unknown value.", iid))
    return out


_CATEGORY_RULES = [
    # (kinds, label pattern, severity, message)
    ({MetricKind.PAYING_CUSTOMERS}, r"\bpilot|\bloi\b|letter of intent|trial|waitlist|beta",
     ERROR, "Pilots, LOIs or trials are counted as paying customers."),
    ({MetricKind.REVENUE}, r"\barr\b|run[- ]?rate|\bmrr\b|booking|\bgmv\b|pipeline|contract value",
     ERROR, "ARR, bookings, GMV or pipeline is labelled as revenue."),
    ({MetricKind.ARR, MetricKind.MRR}, r"booking|pipeline|\bgmv\b|\btcv\b",
     ERROR, "Bookings, pipeline or GMV is labelled as recurring revenue."),
    ({MetricKind.CASH_RECEIVED}, r"commit|soft|verbal|interest|circled|pledge|indicat",
     ERROR, "Commitments or interest are counted as cash received."),
    ({MetricKind.COMMITMENT_SIGNED}, r"verbal|soft|interest|indicat|circled",
     WARNING, "Verbal or soft interest is labelled as a signed commitment."),
]


def _check_categories(analysis: Analysis) -> list[Issue]:
    out = []
    for key, s in _sections(analysis):
        for m in s.metrics:
            text = f"{m.label} {m.value or ''}".lower()
            for kinds, pattern, sev, msg in _CATEGORY_RULES:
                if m.kind in kinds and re.search(pattern, text):
                    out.append(Issue(sev, key, f"{msg} ({m.label})", m.id))
            if m.kind in (MetricKind.TAM, MetricKind.SAM, MetricKind.SOM):
                tag = m.kind.value.upper()
                other = {"TAM", "SAM", "SOM"} - {tag}
                if any(re.search(rf"\b{o}\b", m.label.upper()) for o in other):
                    out.append(Issue(ERROR, key, f"Label '{m.label}' does not match its kind {tag}.", m.id))
    return out


# Words that mark a figure as forward-looking. Metric labels are short, so a
# broader list is safe there; free-text points use the narrower list.
_FORECAST_METRIC = r"project|forecast|\btarget|expect|\bplan(?:ned)?\b|\bgoal|by end of|\b20\d\d\s?E\b"
_FORECAST_TEXT = r"project(?:ed|ion)|forecast|expected to|\b20\d\d\s?E\b"


def _check_timeframes(analysis: Analysis, today: date) -> list[Issue]:
    out = []
    for key, item in iter_claims(analysis):
        tf = getattr(item, "timeframe", None)
        if tf != Timeframe.ACTUAL:
            continue
        if isinstance(item, Metric):
            text, pattern = f"{item.label} {item.period or ''} {item.value or ''}", _FORECAST_METRIC
        else:
            text, pattern = item.text, _FORECAST_TEXT
        if re.search(pattern, text, re.IGNORECASE):
            out.append(Issue(ERROR, key, "Projected figure is marked as an actual result.", _item_id(item)))
            continue
        years = [int(y) for y in re.findall(r"\b(20\d\d)\b", text)]
        if years and max(years) > today.year:
            out.append(Issue(ERROR, key, f"Actual result dated in the future ({max(years)}).", _item_id(item)))
    return out


# Percentages that cannot exceed 100. Retention is handled separately because
# net revenue retention legitimately can.
_PERCENT_BOUNDED = {MetricKind.GROSS_MARGIN}


def _check_units_and_currency(analysis, metrics, section_of) -> list[Issue]:
    out = []
    base = (analysis.reporting_currency or "").upper() or None
    currencies = {}
    for mid, m in metrics.items():
        key = section_of[mid]
        cur = metric_currency(m)
        if cur:
            currencies.setdefault(cur, []).append(mid)
        num = metric_number(m)
        if m.value and num is None and m.evidence in (Evidence.REPORTED, Evidence.CALCULATED) and re.search(r"\d", m.value):
            out.append(Issue(INFO, key, f"Could not read a number from '{m.value}'.", mid))
        if m.value and m.numeric_value is not None:
            parsed = parse_percent(m.value) if "%" in m.value else parse_amount(m.value)[0]
            if parsed is not None and not close(parsed, m.numeric_value, rel=0.06):
                out.append(Issue(ERROR, key, f"Displayed value '{m.value}' does not match numeric value "
                                             f"{m.numeric_value:,.4g} (check units).", mid))
        is_pct = m.unit == "%" or (m.value or "").strip().endswith("%")
        if is_pct and num is not None and m.kind in _PERCENT_BOUNDED and not (0 <= num <= 100):
            out.append(Issue(ERROR, key, f"{m.label} of {num:g}% is outside 0-100%.", mid))
        if m.kind == MetricKind.RETENTION and is_pct and num is not None:
            if num < 0 or (num > 100 and "net" not in m.label.lower()):
                out.append(Issue(ERROR, key, f"{m.label} of {num:g}% is impossible unless it is net revenue retention.", mid))
        if m.evidence in (Evidence.REPORTED, Evidence.CALCULATED) and m.kind in (
            MetricKind.REVENUE, MetricKind.ARR, MetricKind.MRR, MetricKind.PAYING_CUSTOMERS,
            MetricKind.GROWTH_RATE, MetricKind.BURN,
        ) and not m.period:
            out.append(Issue(WARNING, key, f"{m.label} has no reporting period or as-of date.", mid))
    if len(currencies) > 1:
        detail = ", ".join(f"{c}: {', '.join(ids)}" for c, ids in currencies.items())
        out.append(Issue(WARNING, "general", f"Mixed currencies, not converted: {detail}."))
    elif base and currencies and base not in currencies:
        out.append(Issue(WARNING, "general", f"Figures are in {next(iter(currencies))} but reporting currency is {base}."))
    return out


def _check_calculations(metrics, section_of) -> list[Issue]:
    out = []
    values = {mid: v for mid, m in metrics.items() if (v := metric_number(m)) is not None
              and m.evidence in (Evidence.REPORTED, Evidence.CALCULATED)}
    for mid, m in metrics.items():
        if m.evidence != Evidence.CALCULATED:
            continue
        key = section_of[mid]
        missing = [i for i in m.inputs if i not in metrics]
        if missing:
            out.append(Issue(WARNING, key, f"Calculation inputs not found: {', '.join(missing)}.", mid))
        if not m.expression:
            continue
        try:
            result = evaluate_expression(m.expression, values)
        except KeyError as exc:
            out.append(Issue(WARNING, key, f"Cannot check calculation: input '{exc.args[0]}' has no reported value.", mid))
            continue
        except (ValueError, SyntaxError, ZeroDivisionError):
            out.append(Issue(WARNING, key, f"Cannot evaluate expression '{m.expression}'.", mid))
            continue
        shown = metric_number(m)
        if shown is None:
            continue
        if m.unit == "%" and abs(result) <= 1.5 and shown > 1.5:
            result *= 100
        if not close(result, shown, rel=0.03):
            out.append(Issue(ERROR, key, f"Arithmetic check failed: {m.expression} = {result:,.4g}, "
                                         f"but {shown:,.4g} is shown.", mid))
    return out


def _first(metrics, kind, *, usable=True):
    for m in metrics.values():
        if m.kind == kind and (not usable or m.evidence in (Evidence.REPORTED, Evidence.CALCULATED, Evidence.ESTIMATED)):
            return m
    return None


def _check_market(analysis, metrics, section_of) -> list[Issue]:
    out = []
    tam, sam, som = (_first(metrics, k) for k in (MetricKind.TAM, MetricKind.SAM, MetricKind.SOM))
    ladder = [(n, m) for n, m in (("TAM", tam), ("SAM", sam), ("SOM", som)) if m and metric_number(m) is not None]
    for (n1, m1), (n2, m2) in zip(ladder, ladder[1:]):
        c1, c2 = metric_currency(m1), metric_currency(m2)
        if c1 and c2 and c1 != c2:
            out.append(Issue(WARNING, "market_size", f"{n1} and {n2} are in different currencies; not compared."))
            continue
        if metric_number(m2) > metric_number(m1):
            out.append(Issue(ERROR, "market_size", f"{n2} ({m2.value}) is larger than {n1} ({m1.value})."))
    for m in (tam, sam, som):
        if m and m.evidence != Evidence.NOT_PROVIDED:
            gaps = [f for f, v in (("geography", m.geography), ("year", m.year), ("sizing method", m.sizing_method)) if not v]
            if gaps:
                out.append(Issue(INFO, "market_size", f"{m.label}: no {', '.join(gaps)} given.", m.id))
    return out


def _check_fundraise(metrics, section_of) -> list[Issue]:
    out = []
    num = lambda m: metric_number(m) if m else None  # noqa: E731
    target = _first(metrics, MetricKind.RAISE_TARGET)
    cash = _first(metrics, MetricKind.CASH_RECEIVED)
    signed = _first(metrics, MetricKind.COMMITMENT_SIGNED)
    verbal = _first(metrics, MetricKind.COMMITMENT_VERBAL)
    remaining = _first(metrics, MetricKind.RAISE_REMAINING)
    pre, post = _first(metrics, MetricKind.PRE_MONEY), _first(metrics, MetricKind.POST_MONEY)

    t, c, r = num(target), num(cash), num(remaining)
    if t is not None and c is not None and r is not None and not close(t - c, r):
        out.append(Issue(ERROR, "fundraise", f"Raise target {target.value} minus cash received {cash.value} "
                                             f"does not equal remaining {remaining.value}."))
    for m in (cash, signed, verbal):
        if t is not None and num(m) is not None and num(m) > t * 1.001:
            out.append(Issue(WARNING, "fundraise", f"{m.label} ({m.value}) exceeds the raise target ({target.value}).", m.id))
    if c is not None and num(signed) is not None and c > num(signed) * 1.001 and signed.evidence == Evidence.REPORTED:
        out.append(Issue(INFO, "fundraise", "Cash received exceeds signed commitments; confirm whether cash is part of commitments."))
    if pre and post and t is not None and num(pre) is not None and num(post) is not None:
        if not close(num(pre) + t, num(post)):
            out.append(Issue(ERROR, "fundraise", f"Pre-money {pre.value} + raise {target.value} does not equal "
                                                 f"post-money {post.value}."))
    if target is None:
        out.append(Issue(INFO, "fundraise", "No raise target found."))
    return out


def _check_use_of_funds(analysis: Analysis, metrics) -> list[Issue]:
    out = []
    uof: UseOfFundsSection = analysis.sections.use_of_funds
    allocs = [a for a in uof.allocations if a.evidence != Evidence.NOT_PROVIDED]
    if not allocs:
        return out
    pcts = [a.percent for a in allocs if a.percent is not None]
    if pcts and len(pcts) == len(allocs) and not close(sum(pcts), 100, abs_tol=1.0, rel=0):
        out.append(Issue(ERROR, "use_of_funds", f"Allocation percentages sum to {sum(pcts):g}%, not 100%."))
    target = _first(metrics, MetricKind.RAISE_TARGET)
    t = metric_number(target) if target else None
    amounts = [a.amount_value if a.amount_value is not None else parse_amount(a.amount)[0] for a in allocs]
    if t and all(v is not None for v in amounts):
        total = sum(amounts)
        if not close(total, t):
            out.append(Issue(ERROR, "use_of_funds", f"Allocation amounts total {total:,.0f}, but the raise is {target.value}."))
    if t:
        for a, amt in zip(allocs, amounts):
            if a.percent is not None and amt is not None and not close(a.percent / 100 * t, amt, rel=0.03):
                out.append(Issue(WARNING, "use_of_funds", f"{a.category}: {a.percent:g}% of the raise is "
                                                          f"{a.percent / 100 * t:,.0f}, not {amt:,.0f}.", a.category))
    for a in allocs:
        if a.evidence == Evidence.ESTIMATED and not a.proposed:
            out.append(Issue(WARNING, "use_of_funds", f"Estimated allocation '{a.category}' should be marked proposed.", a.category))
    return out

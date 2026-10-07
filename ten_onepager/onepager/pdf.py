"""One-page investor PDF (US Letter) with ReportLab.

Layout: company name and one-sentence thesis, a strip of 3-5 key metrics, the
nine sections in two balanced columns, and the TEN Capital footer.

Only PDF-eligible claims are printed (see evidence.pdf_eligible): reported and
calculated figures, and estimates the user approved. Approved estimates,
forecasts and proposed allocations carry a visible label; reported figures
carry a small page/slide reference.

Fitting: sections are measured and packed into two columns. If they do not
fit, content is shortened step by step (fewer points, fewer metrics, first
sentence only, then repetition removed). Body text never drops below 9 pt. If
it still does not fit, ContentOverflowError names the sections to shorten.
Everything is drawn on a single canvas page, so a second page cannot occur.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Flowable, Frame, Paragraph, Spacer, Table, TableStyle

from .config import ASSETS_DIR
from .evidence import label_for, pdf_eligible
from .models import (
    SECTION_TITLES,
    Allocation,
    Analysis,
    Evidence,
    Metric,
    MetricKind,
    Point,
    Section,
    TeamSection,
    UseOfFundsSection,
    all_metrics,
    iter_sections,
)

# TEN Capital brand palette.
INK = colors.HexColor("#000000")
BODY = colors.HexColor("#4B4F58")
MUTED = colors.HexColor("#7A7A7A")
CORAL = colors.HexColor("#ED5644")
TEAL = colors.HexColor("#4FC4D6")
AMBER = colors.HexColor("#F1A31F")
CTA = "#FFC730"
GRAY_BG = colors.HexColor("#F7F7F7")
RULE = colors.HexColor("#CBD6E2")

PAGE_W, PAGE_H = letter
MARGIN_X = 0.5 * inch
MARGIN_TOP = 0.42 * inch
FOOTER_TOP = 0.62 * inch          # columns end above this line
GUTTER = 0.28 * inch
COL_W = (PAGE_W - 2 * MARGIN_X - GUTTER) / 2
SECTION_GAP = 7

BODY_SIZE = 9.0                   # never below 9 pt
HEADING_SIZE = 10.5
CONFIDENTIAL = "Confidential – for recipients only."
DISCLAIMER = "Not investment advice. TEN Capital Network is not a registered broker-dealer."

LOGO_PATH = ASSETS_DIR / "TEN_Capital_logo_footer.png"
FONT_DIR = ASSETS_DIR / "fonts"


class ContentOverflowError(Exception):
    """Content cannot fit on one page without going below 9 pt."""

    def __init__(self, sections: list[tuple[str, float]], overflow_pts: float):
        self.sections = sections
        self.overflow_pts = overflow_pts
        names = ", ".join(t for t, _ in sections[:3])
        super().__init__(
            f"The content does not fit on one page even after shortening (about {overflow_pts:.0f} pt too tall). "
            f"Shorten these sections first: {names}."
        )


@dataclass
class RenderResult:
    pdf: bytes
    trim_level: int
    excluded: dict[str, int] = field(default_factory=dict)
    columns_top: float = 0.0
    columns_bottom: float = 0.0
    font: str = ""


# --------------------------------------------------------------------------- #
# Fonts and styles
# --------------------------------------------------------------------------- #
_FONTS: dict[str, str] | None = None


def fonts() -> dict[str, str]:
    """Register Open Sans when its files are present; fall back to Helvetica."""
    global _FONTS
    if _FONTS is not None:
        return _FONTS
    files = {w: FONT_DIR / f"OpenSans-{w}.ttf" for w in ("Regular", "Bold", "SemiBold", "Italic")}
    try:
        if not all(f.is_file() for f in files.values()):
            raise FileNotFoundError
        for w, f in files.items():
            pdfmetrics.registerFont(TTFont(f"OpenSans-{w}", str(f)))
        pdfmetrics.registerFontFamily("OpenSans", normal="OpenSans-Regular", bold="OpenSans-Bold",
                                      italic="OpenSans-Italic", boldItalic="OpenSans-Bold")
        _FONTS = {"regular": "OpenSans-Regular", "bold": "OpenSans-Bold",
                  "semibold": "OpenSans-SemiBold", "italic": "OpenSans-Italic", "family": "Open Sans"}
    except Exception:  # noqa: BLE001 - any font problem falls back to built-ins
        _FONTS = {"regular": "Helvetica", "bold": "Helvetica-Bold", "semibold": "Helvetica-Bold",
                  "italic": "Helvetica-Oblique", "family": "Helvetica"}
    return _FONTS


def _styles() -> dict[str, ParagraphStyle]:
    f = fonts()
    s = {
        "name": ParagraphStyle("name", fontName=f["bold"], fontSize=20, leading=23, textColor=INK),
        "thesis": ParagraphStyle("thesis", fontName=f["regular"], fontSize=10, leading=13, textColor=INK),
        "heading": ParagraphStyle("heading", fontName=f["bold"], fontSize=HEADING_SIZE, leading=12.5,
                                  textColor=INK),
        "body": ParagraphStyle("body", fontName=f["regular"], fontSize=BODY_SIZE, leading=11.6, textColor=BODY),
        "bullet": ParagraphStyle("bullet", fontName=f["regular"], fontSize=BODY_SIZE, leading=11.6,
                                 textColor=BODY, leftIndent=9, firstLineIndent=-9, spaceBefore=1.2),
        "missing": ParagraphStyle("missing", fontName=f["italic"], fontSize=BODY_SIZE, leading=11.6,
                                  textColor=MUTED, spaceBefore=1.2),
        "stat_label": ParagraphStyle("stat_label", fontName=f["semibold"], fontSize=7.5, leading=9,
                                     textColor=INK, alignment=TA_CENTER),
        "stat_value": ParagraphStyle("stat_value", fontName=f["bold"], fontSize=17, leading=20,
                                     textColor=CORAL, alignment=TA_CENTER),
        "stat_note": ParagraphStyle("stat_note", fontName=f["regular"], fontSize=6.5, leading=8,
                                    textColor=MUTED, alignment=TA_CENTER),
    }
    return s


def _e(text) -> str:
    return escape("" if text is None else str(text))


def _short_ref(ref: str) -> str:
    m = re.match(r"\s*(slide|page)\s*(\d+)", ref, re.I)
    if m:
        return ("S" if m.group(1).lower() == "slide" else "p.") + m.group(2)
    return ref.strip()


def _refs(item) -> str:
    refs = [_short_ref(r) for r in getattr(item, "source_refs", []) if r.strip()]
    if not refs:
        return ""
    return f' <font size="6.5" color="#7A7A7A">[{_e(", ".join(refs))}]</font>'


def _tag(item) -> str:
    """Visible label chip for estimates, forecasts and proposed items."""
    parts = [t for t in label_for(item).split(" · ") if t and t != "CALC."]
    out = "".join(f' <font size="6.5" backColor="{CTA}" color="#000000"><b>&nbsp;{t}&nbsp;</b></font>' for t in parts)
    if item.evidence == Evidence.CALCULATED:
        out += ' <font size="6.5" color="#7A7A7A">calc.</font>'
    return out


def _first_sentence(text: str) -> str:
    m = re.match(r"(.+?[.!?])(\s|$)", text.strip())
    return m.group(1) if m else text.strip()


# --------------------------------------------------------------------------- #
# Trimming levels: (points, metrics, members, milestones, missing, first_sentence, drop_strip_dupes)
# --------------------------------------------------------------------------- #
TRIM_LEVELS = [
    (2, 5, 4, 2, 2, False, False),
    (2, 4, 4, 2, 2, False, False),
    (1, 3, 4, 2, 1, False, False),
    (1, 3, 3, 1, 1, False, True),
    (1, 2, 3, 1, 1, True, True),
    (0, 2, 3, 1, 1, True, True),
    (0, 2, 2, 0, 1, True, True),
    (0, 1, 2, 0, 1, True, True),
]


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def render_pdf(analysis: Analysis, *, today: date | None = None) -> RenderResult:
    today = today or date.today()
    st = _styles()
    exit_ok = bool(analysis.meta.get("exit_scenarios_requested"))
    strip_metrics = _key_metrics(analysis, exit_ok)
    strip_ids = {m.id for m in strip_metrics}

    header = _header_flowables(analysis, st)
    header_h = sum(_height(f, PAGE_W - 2 * MARGIN_X) for f in header)
    strip = _strip_table(strip_metrics, st) if strip_metrics else None
    strip_h = _height(strip, PAGE_W - 2 * MARGIN_X) if strip else 0
    columns_top = PAGE_H - MARGIN_TOP - header_h - (strip_h + 10 if strip else 4)
    avail = columns_top - FOOTER_TOP

    # Shorten one section at a time: always the tallest section in the taller
    # column, so short sections keep their detail.
    sections = list(iter_sections(analysis))
    cache: dict[tuple[int, int], tuple[list, float]] = {}

    def block(i: int, level: int) -> tuple[list, float]:
        if (i, level) not in cache:
            key, s = sections[i]
            fl = _section_flowables(key, s, TRIM_LEVELS[level], strip_ids, exit_ok, st)
            cache[(i, level)] = (fl, sum(_height(f, COL_W) for f in fl) + SECTION_GAP)
        return cache[(i, level)]

    levels = [0] * len(sections)
    top = len(TRIM_LEVELS) - 1
    while True:
        built = [block(i, lv) for i, lv in enumerate(levels)]
        heights = [h for _, h in built]
        split, tallest = _best_split(heights)
        if tallest <= avail:
            blocks = [(SECTION_TITLES[k], fl) for (k, _), (fl, _) in zip(sections, built)]
            pdf = _draw(analysis, header, strip, blocks, split, columns_top, avail, today)
            return RenderResult(pdf, max(levels), _excluded_counts(analysis, exit_ok), columns_top, FOOTER_TOP,
                                fonts()["family"])
        taller = range(split) if sum(heights[:split]) >= sum(heights[split:]) else range(split, len(heights))
        candidates = [i for i in taller if levels[i] < top] or [i for i in range(len(levels)) if levels[i] < top]
        if not candidates:
            ranked = sorted(((SECTION_TITLES[k], h) for (k, _), h in zip(sections, heights)), key=lambda x: -x[1])
            raise ContentOverflowError(ranked, tallest - avail)
        levels[max(candidates, key=lambda i: heights[i])] += 1


def render_to_file(analysis: Analysis, path, **kwargs) -> RenderResult:
    result = render_pdf(analysis, **kwargs)
    with open(path, "wb") as fh:
        fh.write(result.pdf)
    return result


# --------------------------------------------------------------------------- #
# Building blocks
# --------------------------------------------------------------------------- #
def _height(flowable, width) -> float:
    _, h = flowable.wrap(width, 10_000)
    return h + flowable.getSpaceBefore() + flowable.getSpaceAfter()


def _best_split(heights: list[float]) -> tuple[int, float]:
    """Split the ordered sections into two columns minimizing the taller one."""
    best = (1, float("inf"))
    for k in range(1, len(heights)):
        tallest = max(sum(heights[:k]), sum(heights[k:]))
        if tallest < best[1]:
            best = (k, tallest)
    return best


class _Bar(Flowable):
    """Short coral accent bar (TEN heading treatment)."""

    def __init__(self, width=26, thickness=2.2, space_after=3):
        super().__init__()
        self.bar_w, self.thickness, self.space_after = width, thickness, space_after

    def wrap(self, aw, ah):
        return aw, self.thickness + self.space_after

    def draw(self):
        self.canv.setFillColor(CORAL)
        self.canv.rect(0, self.space_after, self.bar_w, self.thickness, stroke=0, fill=1)


def _header_flowables(analysis: Analysis, st) -> list:
    name = _e(analysis.company_name or "Company")
    if analysis.meta.get("demo"):
        name += f' <font size="7" backColor="{CTA}" color="#000000"><b>&nbsp;FICTIONAL SAMPLE DATA&nbsp;</b></font>'
    out = [_Bar(width=34, thickness=3, space_after=4), Paragraph(name, st["name"])]
    if analysis.thesis.strip():
        out += [Spacer(1, 2), Paragraph(_e(analysis.thesis.strip()), st["thesis"])]
    return out


def _key_metrics(analysis: Analysis, exit_ok: bool) -> list[Metric]:
    metrics = all_metrics(analysis)
    section_of = {m.id: k for k, s in iter_sections(analysis) for m in s.metrics}
    chosen = [metrics[i] for i in analysis.key_metric_ids
              if i in metrics and pdf_eligible(section_of[i], metrics[i], exit_scenarios=exit_ok)]
    if len(chosen) < 3:  # fill from the most decision-relevant sections
        for key in ("traction", "fundraise", "market_size", "solution", "problem"):
            for m in getattr(analysis.sections, key).metrics:
                if len(chosen) >= 3:
                    break
                if m not in chosen and m.evidence in (Evidence.REPORTED, Evidence.CALCULATED) \
                        and pdf_eligible(key, m, exit_scenarios=exit_ok):
                    chosen.append(m)
    return chosen[:5]


def _strip_table(metrics: list[Metric], st) -> Table:
    n = len(metrics)
    labels = [Paragraph(_e(m.label), st["stat_label"]) for m in metrics]
    values = [Paragraph(_e(m.value), st["stat_value"]) for m in metrics]
    notes = []
    for m in metrics:
        bits = [b for b in (_e(m.period) if m.period else "",) if b]
        note = " · ".join(bits) + _refs(m) + _tag(m)
        notes.append(Paragraph(note or "&nbsp;", st["stat_note"]))
    width = PAGE_W - 2 * MARGIN_X
    tbl = Table([labels, values, notes], colWidths=[width / n] * n)
    cmds = [
        ("BACKGROUND", (0, 0), (-1, -1), GRAY_BG),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, 0), 7),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 0),
        ("TOPPADDING", (0, 1), (-1, 1), 1),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 0),
        ("TOPPADDING", (0, 2), (-1, 2), 1),
        ("BOTTOMPADDING", (0, 2), (-1, 2), 6),
    ]
    cmds += [("LINEBEFORE", (c, 0), (c, -1), 0.6, RULE) for c in range(1, n)]
    tbl.setStyle(TableStyle(cmds))
    return tbl


def _metric_line(m: Metric) -> str:
    text = f"<b>{_e(m.value)}</b> {_e(m.label)}"
    detail = []
    if m.kind in (MetricKind.TAM, MetricKind.SAM, MetricKind.SOM):
        detail = [d for d in (m.geography, m.year, m.sizing_method) if d]
    elif m.period:
        detail = [m.period]
    if detail:
        text += f" ({_e(', '.join(detail))})"
    if m.evidence == Evidence.ESTIMATED and m.range_low and m.range_high:
        text += f", range {_e(m.range_low)}–{_e(m.range_high)}"
    return text + _refs(m) + _tag(m)


def _point_line(p: Point) -> str:
    prefix = "<i>Analyst view:</i> " if p.attribution == "analyst" else ""
    return prefix + _e(p.text) + _refs(p) + _tag(p)


def _bullet(html: str, st) -> Paragraph:
    return Paragraph(f"•&nbsp;&nbsp;{html}", st["bullet"])


def _section_flowables(key: str, s: Section, spec, strip_ids: set[str], exit_ok: bool, st) -> list:
    n_points, n_metrics, n_members, n_milestones, n_missing, first_only, drop_dupes = spec
    out: list = [
        Paragraph(_e(SECTION_TITLES[key].upper()), st["heading"]),
        _Bar(),
    ]
    summary = s.summary.strip()
    if first_only:
        summary = _first_sentence(summary)
    if summary:
        out.append(Paragraph(_e(summary), st["body"]))

    metrics = [m for m in s.metrics if pdf_eligible(key, m, exit_scenarios=exit_ok)]
    if drop_dupes and len(metrics) > 1:
        metrics = [m for m in metrics if m.id not in strip_ids] or metrics[:1]
    out += [_bullet(_metric_line(m), st) for m in metrics[:n_metrics]]

    if isinstance(s, TeamSection):
        for mem in s.members[:n_members]:
            line = f"<b>{_e(mem.name)}</b>, {_e(mem.role)}"
            if mem.background:
                line += f" — {_e(mem.background)}"
            out.append(_bullet(line + _refs(mem), st))

    if isinstance(s, UseOfFundsSection):
        allocs = [a for a in s.allocations if pdf_eligible(key, a)]
        if allocs:
            out.append(_bullet(_allocation_line(allocs), st))
        milestones = [p for p in s.milestones if pdf_eligible(key, p)]
        out += [_bullet(_point_line(p), st) for p in milestones[:n_milestones]]

    seen = {summary.lower()}
    points = []
    for p in s.points:
        if pdf_eligible(key, p) and p.text.strip().lower() not in seen:
            seen.add(p.text.strip().lower())
            points.append(p)
    out += [_bullet(_point_line(p), st) for p in points[:n_points]]

    gaps = list(s.missing) + [m.label for m in s.metrics if m.evidence == Evidence.NOT_PROVIDED]
    gaps = list(dict.fromkeys(g for g in gaps if g.strip()))
    content_count = len(out) - 2
    if gaps and n_missing:
        out.append(Paragraph("Not provided: " + _e("; ".join(gaps[:n_missing + (1 if content_count == 0 else 0)])),
                             st["missing"]))
    elif content_count == 0:
        out.append(Paragraph("Not provided in the deck.", st["missing"]))
    return out


def _allocation_line(allocs: list[Allocation]) -> str:
    parts = []
    for a in allocs:
        amount = a.percent is not None and f"{a.percent:g}%" or a.amount or ""
        parts.append(f"{_e(a.category)} <b>{_e(amount)}</b>")
    refs = sorted({r for a in allocs for r in a.source_refs})
    tags = ""
    if any(a.proposed for a in allocs):
        tags += f' <font size="6.5" backColor="{CTA}" color="#000000"><b>&nbsp;PROPOSED&nbsp;</b></font>'
    if any(a.evidence == Evidence.ESTIMATED for a in allocs):
        tags += f' <font size="6.5" backColor="{CTA}" color="#000000"><b>&nbsp;EST.&nbsp;</b></font>'
    ref_html = f' <font size="6.5" color="#7A7A7A">[{_e(", ".join(_short_ref(r) for r in refs))}]</font>' if refs else ""
    return "Allocation: " + " · ".join(parts) + ref_html + tags


def _excluded_counts(analysis: Analysis, exit_ok: bool) -> dict[str, int]:
    from .models import iter_claims

    counts = {"unapproved_estimates": 0, "conflicting": 0, "not_provided": 0, "prohibited_or_not_requested": 0}
    for key, item in iter_claims(analysis):
        if pdf_eligible(key, item, exit_scenarios=exit_ok):
            continue
        if item.evidence == Evidence.ESTIMATED and not item.approved:
            counts["unapproved_estimates"] += 1
        elif item.evidence == Evidence.CONFLICTING:
            counts["conflicting"] += 1
        elif item.evidence == Evidence.NOT_PROVIDED:
            counts["not_provided"] += 1
        else:
            counts["prohibited_or_not_requested"] += 1
    return counts


# --------------------------------------------------------------------------- #
# Drawing
# --------------------------------------------------------------------------- #
def _draw(analysis, header, strip, blocks, split, columns_top, avail, today) -> bytes:
    buf = io.BytesIO()
    c = Canvas(buf, pagesize=letter)
    c.setTitle(f"{analysis.company_name} — Investor One-Pager")
    c.setAuthor("TEN Capital Network")

    # Brand accent: thin tri-color bar across the top edge.
    third = PAGE_W / 3
    for i, col in enumerate((CORAL, TEAL, AMBER)):
        c.setFillColor(col)
        c.rect(i * third, PAGE_H - 4, third + 0.5, 4, stroke=0, fill=1)

    content_w = PAGE_W - 2 * MARGIN_X
    top_frame = Frame(MARGIN_X, columns_top, content_w, PAGE_H - MARGIN_TOP - columns_top,
                      leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    items = list(header)
    if strip:
        items += [Spacer(1, 7), strip]
    top_frame.addFromList(items, c)

    for col, chunk in enumerate((blocks[:split], blocks[split:])):
        x = MARGIN_X + col * (COL_W + GUTTER)
        frame = Frame(x, FOOTER_TOP, COL_W, avail, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        flow = []
        for _, fl in chunk:
            flow += fl + [Spacer(1, SECTION_GAP)]
        frame.addFromList(flow, c)
        if any(not isinstance(f, Spacer) for f in flow):
            raise ContentOverflowError([(t, 0) for t, _ in chunk], 0)

    _draw_footer(c, analysis, today)
    c.showPage()
    c.save()
    return buf.getvalue()


def _draw_footer(c: Canvas, analysis: Analysis, today: date) -> None:
    f = fonts()
    c.setStrokeColor(RULE)
    c.setLineWidth(0.5)
    c.line(MARGIN_X, FOOTER_TOP - 4, PAGE_W - MARGIN_X, FOOTER_TOP - 4)

    # TEN footer pattern: [Title]  [PAGE#]  Compiled on [DATE] by TEN Capital Network  [logo]
    title = f"{analysis.company_name} Investor One-Pager"
    compiled = f"Compiled on {today.strftime('%B %d, %Y').replace(' 0', ' ')} by TEN Capital Network"
    segments = [title, "1", compiled]
    gap = 18
    logo_w, logo_h = 0.67 * inch, 0.25 * inch
    widths = [pdfmetrics.stringWidth(s, f["regular"], 7) for s in segments]
    total = sum(widths) + gap * len(segments) + (logo_w if LOGO_PATH.is_file() else 0)
    x = (PAGE_W - total) / 2
    y = 0.36 * inch
    c.setFont(f["regular"], 7)
    c.setFillColor(MUTED)
    for s, w in zip(segments, widths):
        c.drawString(x, y, s)
        x += w + gap
    if LOGO_PATH.is_file():
        c.drawImage(str(LOGO_PATH), x, y - 6, width=logo_w, height=logo_h, mask="auto",
                    preserveAspectRatio=True)

    c.setFont(f["bold"], 7)
    c.setFillColor(INK)
    line2_a = CONFIDENTIAL
    line2_b = "  " + DISCLAIMER
    wa = pdfmetrics.stringWidth(line2_a, f["bold"], 7)
    wb = pdfmetrics.stringWidth(line2_b, f["regular"], 7)
    x2 = (PAGE_W - wa - wb) / 2
    c.drawString(x2, 0.2 * inch, line2_a)
    c.setFont(f["regular"], 7)
    c.setFillColor(MUTED)
    c.drawString(x2 + wa, 0.2 * inch, line2_b)

"""Stage 3 — PDF rendering.

Lay the analyzed JSON out as a numbers-first investor one-pager using
reportlab's Platypus engine: a header, a deal-snapshot strip of headline
figures, then the nine pitch sections as cards in a 3x3 grid (the opportunity,
the proof, the deal), each leading with large stat tiles. The layout targets a single US-Letter page and
flows to a second page only when the content genuinely doesn't fit.

Estimated figures are set in italic and marked "est.", and the closing note
lists the basis for each estimate.
"""

from __future__ import annotations

import os
from datetime import date
from functools import partial
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Flowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# Palette — TEN Capital Network brand (navy ink, warm-to-teal gradient mark).
INK = colors.HexColor("#16283F")
MUTED = colors.HexColor("#5C6E86")
ACCENT = colors.HexColor("#2A9D9A")
RULE = colors.HexColor("#C4D0E0")
BAR_BG = colors.HexColor("#EAEFF5")
TILE_BG = colors.HexColor("#F3F6FA")

# Brand mark gradient (amber -> coral -> teal), used for the top page bar.
GRADIENT_STOPS = [
    (0.0, colors.HexColor("#F3A22A")),
    (0.5, colors.HexColor("#EE5A4E")),
    (1.0, colors.HexColor("#35BEBB")),
]
GRADIENT_BAR_H = 0.09 * inch

ESTIMATE_NOTE = "Figures in italics marked est. are analyst estimates, not from the deck."

PAGE_W, PAGE_H = letter
MARGIN = 0.5 * inch
CONTENT_W = PAGE_W - 2 * MARGIN
GUTTER = 0.16 * inch

BRAND_NAME = "TEN CAPITAL NETWORK"
LOGO_PATH = os.path.join(os.path.dirname(__file__), "static", "logo-mark.png")
FOOTER_H = 0.34 * inch

# Section grid, one tuple per row: the opportunity, the proof, the deal.
LAYOUT = [
    (("problem", "Problem"), ("solution", "Solution"), ("team", "Team")),
    (("traction", "Traction"), ("market_size", "Market Size"),
     ("competitive_advantage", "Competitive Advantage")),
    (("fundraise", "Fundraise"), ("use_of_funds", "Use of Funds"), ("exit", "Exit")),
]


def _s(v) -> str:
    return "" if v is None else str(v).strip()


def _esc(v) -> str:
    return escape(_s(v))


def _est(text: str, flag: bool) -> str:
    """Return escaped text, appending an italic est. marker when estimated."""
    body = _esc(text)
    return f"{body} <i>(est.)</i>" if flag else body


# --------------------------------------------------------------------------- #
# Styles
# --------------------------------------------------------------------------- #
def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()["Normal"]
    s = {}
    s["title"] = ParagraphStyle(
        "OP_Title", parent=base, fontName="Helvetica-Bold",
        fontSize=20, leading=23, textColor=INK,
    )
    s["tagline"] = ParagraphStyle(
        "OP_Tagline", parent=base, fontName="Helvetica",
        fontSize=10, leading=12.5, textColor=MUTED,
    )
    s["meta"] = ParagraphStyle(
        "OP_Meta", parent=base, fontName="Helvetica",
        fontSize=7.2, leading=9, textColor=MUTED, spaceBefore=1,
    )
    s["snap_value"] = ParagraphStyle(
        "OP_SnapValue", parent=base, fontName="Helvetica-Bold",
        fontSize=15, leading=17, textColor=INK, alignment=TA_CENTER,
    )
    s["snap_label"] = ParagraphStyle(
        "OP_SnapLabel", parent=base, fontName="Helvetica",
        fontSize=6.8, leading=8, textColor=MUTED, alignment=TA_CENTER,
    )
    s["section"] = ParagraphStyle(
        "OP_Section", parent=base, fontName="Helvetica-Bold",
        fontSize=9, leading=11, textColor=ACCENT,
    )
    s["headline"] = ParagraphStyle(
        "OP_Headline", parent=base, fontName="Helvetica-Bold",
        fontSize=8, leading=9.8, textColor=INK, spaceAfter=3,
    )
    s["tile_value"] = ParagraphStyle(
        "OP_TileValue", parent=base, fontName="Helvetica-Bold",
        fontSize=11.5, leading=13.5, textColor=ACCENT, alignment=TA_CENTER,
    )
    s["tile_label"] = ParagraphStyle(
        "OP_TileLabel", parent=base, fontName="Helvetica",
        fontSize=6.2, leading=7.2, textColor=MUTED, alignment=TA_CENTER,
    )
    s["bullet"] = ParagraphStyle(
        "OP_Bullet", parent=base, fontName="Helvetica",
        fontSize=7.4, leading=9.1, textColor=INK,
        leftIndent=8, firstLineIndent=-8, spaceAfter=1.2,
    )
    s["note"] = ParagraphStyle(
        "OP_Note", parent=base, fontName="Helvetica-Oblique",
        fontSize=6.6, leading=8.2, textColor=MUTED,
    )
    return s


# --------------------------------------------------------------------------- #
# Page decoration — brand gradient bar + footer, drawn on every page
# --------------------------------------------------------------------------- #
def _gradient_color_at(t: float) -> colors.Color:
    for (p0, c0), (p1, c1) in zip(GRADIENT_STOPS, GRADIENT_STOPS[1:]):
        if p0 <= t <= p1:
            local_t = 0.0 if p1 == p0 else (t - p0) / (p1 - p0)
            return colors.Color(
                c0.red + (c1.red - c0.red) * local_t,
                c0.green + (c1.green - c0.green) * local_t,
                c0.blue + (c1.blue - c0.blue) * local_t,
            )
    return GRADIENT_STOPS[-1][1]


def _draw_gradient_bar(c) -> None:
    c.saveState()
    segments = 80
    seg_w = PAGE_W / segments
    y = PAGE_H - GRADIENT_BAR_H
    for i in range(segments):
        t = (i + 0.5) / segments
        c.setFillColor(_gradient_color_at(t))
        c.rect(i * seg_w, y, seg_w + 0.75, GRADIENT_BAR_H, stroke=0, fill=1)
    c.restoreState()


def _draw_footer(c, footer_date: str) -> None:
    c.saveState()
    y_rule = FOOTER_H
    c.setStrokeColor(RULE)
    c.setLineWidth(0.6)
    c.line(MARGIN, y_rule, PAGE_W - MARGIN, y_rule)

    y_text = y_rule - 13
    icon_h = icon_w = 10
    name_x = MARGIN
    if os.path.exists(LOGO_PATH):
        c.drawImage(
            LOGO_PATH, MARGIN, y_text - 1.5, width=icon_w, height=icon_h,
            preserveAspectRatio=True, mask="auto",
        )
        name_x = MARGIN + icon_w + 5

    c.setFont("Helvetica-Bold", 6.5)
    c.setFillColor(INK)
    c.drawString(name_x, y_text, BRAND_NAME)

    c.setFont("Helvetica", 6.5)
    c.setFillColor(MUTED)
    c.drawCentredString(PAGE_W / 2, y_text, f"Page {c.getPageNumber()}")
    c.drawRightString(PAGE_W - MARGIN, y_text, footer_date)
    c.restoreState()


def _decorate_page(canvas, doc, footer_date: str) -> None:
    _draw_gradient_bar(canvas)
    _draw_footer(canvas, footer_date)


# --------------------------------------------------------------------------- #
# Flowables
# --------------------------------------------------------------------------- #
class AllocationBars(Flowable):
    """Horizontal percentage bars for use-of-funds allocation."""

    ROW_H = 14
    BAR_H = 5.5
    LABEL_FS = 6.8

    def __init__(self, items: list[dict]):
        super().__init__()
        self.items = items
        self.width = 0
        self.height = self.ROW_H * len(items)

    def wrap(self, availWidth, availHeight):
        self.width = availWidth
        return (availWidth, self.height)

    def draw(self):
        c = self.canv
        y = self.height - self.ROW_H
        for item in self.items:
            cat = _s(item.get("category"))
            est = bool(item.get("is_estimated"))
            try:
                pct = float(item.get("percent") or 0)
            except (TypeError, ValueError):
                pct = 0.0
            pct = max(0.0, min(pct, 100.0))

            c.setFillColor(INK)
            c.setFont("Helvetica-Oblique" if est else "Helvetica", self.LABEL_FS)
            c.drawString(0, y + self.BAR_H + 2, cat + ("  (est.)" if est else ""))
            c.setFont("Helvetica-Bold", self.LABEL_FS)
            c.drawRightString(self.width, y + self.BAR_H + 2, f"{pct:g}%")

            c.setFillColor(BAR_BG)
            c.rect(0, y, self.width, self.BAR_H, stroke=0, fill=1)
            c.setFillColor(ACCENT)
            c.rect(0, y, self.width * (pct / 100.0), self.BAR_H, stroke=0, fill=1)
            y -= self.ROW_H


class _HRule(Flowable):
    def __init__(self, thickness, color, space_before=0, space_after=0):
        super().__init__()
        self.thickness = thickness
        self.color = color
        self.space_before = space_before
        self.space_after = space_after
        self.width = 0
        self.height = thickness + space_before + space_after

    def wrap(self, availWidth, availHeight):
        self.width = availWidth
        return (availWidth, self.height)

    def draw(self):
        c = self.canv
        c.setStrokeColor(self.color)
        c.setLineWidth(self.thickness)
        c.line(0, self.space_after, self.width, self.space_after)


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def render(data: dict, output_path: str) -> None:
    st = _styles()
    story: list = []

    _header(story, data, st)
    _snapshot(story, data.get("deal_snapshot") or [], st)

    for row in LAYOUT:
        n = len(row)
        card_w = (CONTENT_W - GUTTER * (n - 1)) / n
        cells, widths = [], []
        for i, (key, title) in enumerate(row):
            if i:
                cells.append("")
                widths.append(GUTTER)
            cells.append(_card(data.get(key), title, key, st))
            widths.append(card_w)
        tbl = Table([cells], colWidths=widths)
        tbl.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(tbl)

    if _has_estimates(data):
        story.append(_HRule(0.5, RULE, space_after=3))
        story.append(Paragraph(_esc(ESTIMATE_NOTE), st["note"]))
        notes = _basis_columns(data.get("estimate_basis") or [], st)
        if notes:
            story.append(notes)

    doc = SimpleDocTemplate(
        output_path, pagesize=letter,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN + GRADIENT_BAR_H,
        bottomMargin=MARGIN + FOOTER_H - 0.1 * inch,
        title=_s(data.get("company_name")) + " — Deal by the Numbers",
    )
    page_cb = partial(_decorate_page, footer_date=date.today().strftime("%B %d, %Y"))
    doc.build(story, onFirstPage=page_cb, onLaterPages=page_cb)


def _basis_columns(lines: list, st: dict) -> Table | None:
    """Estimate assumptions laid out in two columns to save vertical space."""
    paras = [Paragraph("•&nbsp;" + _esc(b), st["note"]) for b in lines if _s(b)][:6]
    if not paras:
        return None
    half = (len(paras) + 1) // 2
    col_w = (CONTENT_W - GUTTER) / 2
    tbl = Table([[paras[:half], "", paras[half:]]], colWidths=[col_w, GUTTER, col_w])
    tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
    ]))
    return tbl


# --------------------------------------------------------------------------- #
# Header and deal snapshot
# --------------------------------------------------------------------------- #
def _header(story: list, data: dict, st: dict) -> None:
    story.append(Paragraph(_esc(_s(data.get("company_name")) or "Company"), st["title"]))
    tagline = _s(data.get("tagline"))
    if tagline:
        story.append(Paragraph(_esc(tagline), st["tagline"]))
    meta = [m for m in (_s(data.get("sector")), _s(data.get("stage")),
                        _s(data.get("source_attribution"))) if m]
    if meta:
        story.append(Paragraph("&nbsp;&nbsp;·&nbsp;&nbsp;".join(_esc(m) for m in meta), st["meta"]))
    story.append(_HRule(1.3, ACCENT, space_before=4, space_after=6))


def _value_para(metric: dict, style: ParagraphStyle) -> Paragraph:
    value = _esc(metric.get("value"))
    if metric.get("is_estimated"):
        value = f"<i>{value}</i>"
    return Paragraph(value, style)


def _label_para(metric: dict, style: ParagraphStyle) -> Paragraph:
    label = _esc(metric.get("label"))
    if metric.get("is_estimated"):
        label += " <i>· est.</i>"
    return Paragraph(label, style)


def _snapshot(story: list, metrics: list, st: dict) -> None:
    metrics = [m for m in metrics if _s(m.get("value"))][:6]
    if not metrics:
        return
    n = len(metrics)
    tbl = Table(
        [[_value_para(m, st["snap_value"]) for m in metrics],
         [_label_para(m, st["snap_label"]) for m in metrics]],
        colWidths=[CONTENT_W / n] * n,
    )
    cmds = [
        ("BACKGROUND", (0, 0), (-1, -1), BAR_BG),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, 0), 7),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 1),
        ("TOPPADDING", (0, 1), (-1, 1), 0),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 7),
    ]
    for col in range(1, n):
        cmds.append(("LINEBEFORE", (col, 0), (col, -1), 0.6, RULE))
    tbl.setStyle(TableStyle(cmds))
    story.append(tbl)
    story.append(Spacer(1, 9))


# --------------------------------------------------------------------------- #
# Section cards
# --------------------------------------------------------------------------- #
def _card(node, title: str, key: str, st: dict) -> list:
    node = node or {}
    out: list = [
        Paragraph(_esc(title).upper(), st["section"]),
        _HRule(0.8, ACCENT, space_before=1, space_after=3),
    ]
    headline = _s(node.get("headline"))
    if headline:
        out.append(Paragraph(_esc(headline), st["headline"]))

    tiles = _tiles(node.get("metrics") or [], st)
    if tiles:
        out.append(tiles)
        out.append(Spacer(1, 4))

    if key == "use_of_funds":
        alloc = [a for a in node.get("allocation") or [] if _s(a.get("category"))]
        if alloc:
            out.append(AllocationBars(alloc))
            out.append(Spacer(1, 3))

    if key == "team":
        for m in node.get("members") or []:
            name, role, cred = _s(m.get("name")), _s(m.get("role")), _s(m.get("credential"))
            if not (name or role):
                continue
            line = f"<b>{_esc(name)}</b>" + (f", {_esc(role)}" if role else "")
            if cred:
                line += f" — {_esc(cred)}"
            out.append(Paragraph(f"•&nbsp;&nbsp;{line}", st["bullet"]))

    for p in node.get("points") or []:
        text = _s(p.get("text"))
        if text:
            out.append(Paragraph(f"•&nbsp;&nbsp;{_est(text, bool(p.get('is_estimated')))}", st["bullet"]))
    return out


def _tiles(metrics: list, st: dict) -> "_TileRow | None":
    metrics = [m for m in metrics if _s(m.get("value"))][:3]
    if not metrics:
        return None
    return _TileRow(
        [[_value_para(m, st["tile_value"]) for m in metrics],
         [_label_para(m, st["tile_label"]) for m in metrics]],
        len(metrics), gap=3,
    )


class _TileRow(Flowable):
    """A row of stat tiles that sizes its columns to the space it is given."""

    def __init__(self, rows, n, gap):
        super().__init__()
        self.rows = rows
        self.n = n
        self.gap = gap
        self._table = None

    def _build(self, width):
        col_w = (width - self.gap * (self.n - 1)) / self.n
        widths, values, labels = [], [], []
        for i in range(self.n):
            if i:
                widths.append(self.gap)
                values.append("")
                labels.append("")
            widths.append(col_w)
            values.append(self.rows[0][i])
            labels.append(self.rows[1][i])
        tbl = Table([values, labels], colWidths=widths)
        cmds = [
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 1.5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 1.5),
            ("TOPPADDING", (0, 0), (-1, 0), 5),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 0),
            ("TOPPADDING", (0, 1), (-1, 1), 1),
            ("BOTTOMPADDING", (0, 1), (-1, 1), 5),
        ]
        for i in range(self.n):
            col = i * 2
            cmds.append(("BACKGROUND", (col, 0), (col, -1), TILE_BG))
        tbl.setStyle(TableStyle(cmds))
        return tbl

    def wrap(self, availWidth, availHeight):
        self._table = self._build(availWidth)
        w, h = self._table.wrap(availWidth, availHeight)
        self.width, self.height = w, h
        return w, h

    def draw(self):
        self._table.drawOn(self.canv, 0, 0)


# --------------------------------------------------------------------------- #
# Estimate detection (for the closing note)
# --------------------------------------------------------------------------- #
def _has_estimates(node) -> bool:
    if isinstance(node, dict):
        return bool(node.get("is_estimated")) or any(_has_estimates(v) for v in node.values())
    if isinstance(node, list):
        return any(_has_estimates(v) for v in node)
    return False

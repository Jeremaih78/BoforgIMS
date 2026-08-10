"""Build the Boforg deployment handbook PDF from its Markdown source."""

from __future__ import annotations

import html
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents


HERE = Path(__file__).resolve().parent
SOURCE = HERE / "Boforg_Production_Deployment_Guide.md"
OUTPUT = HERE / "Boforg_Production_Deployment_Guide.pdf"


def register_fonts() -> tuple[str, str, str]:
    candidates = [
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    bold_candidates = [
        Path("C:/Windows/Fonts/arialbd.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ]
    mono_candidates = [
        Path("C:/Windows/Fonts/consola.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
    ]
    regular = next((p for p in candidates if p.exists()), None)
    bold = next((p for p in bold_candidates if p.exists()), None)
    mono = next((p for p in mono_candidates if p.exists()), None)
    if regular and bold and mono:
        pdfmetrics.registerFont(TTFont("BoforgSans", str(regular)))
        pdfmetrics.registerFont(TTFont("BoforgSansBold", str(bold)))
        pdfmetrics.registerFont(TTFont("BoforgMono", str(mono)))
        return "BoforgSans", "BoforgSansBold", "BoforgMono"
    return "Helvetica", "Helvetica-Bold", "Courier"


REGULAR, BOLD, MONO = register_fonts()
INK = colors.HexColor("#17233C")
BLUE = colors.HexColor("#1459A6")
LIGHT_BLUE = colors.HexColor("#EAF2FB")
GOLD = colors.HexColor("#E0A321")
PALE_GOLD = colors.HexColor("#FFF7DF")
RED = colors.HexColor("#A62B2B")
PALE_RED = colors.HexColor("#FDECEC")
GREY = colors.HexColor("#667085")
LIGHT_GREY = colors.HexColor("#F4F6F8")
LINE = colors.HexColor("#D7DEE8")


class HandbookDocTemplate(BaseDocTemplate):
    def __init__(self, filename: str):
        super().__init__(
            filename,
            pagesize=A4,
            rightMargin=18 * mm,
            leftMargin=18 * mm,
            topMargin=19 * mm,
            bottomMargin=18 * mm,
            title="Boforg Production Deployment Guide",
            author="Boforg Technologies Private Limited",
            subject="Multi-Subdomain Django Deployment",
        )
        frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id="body")
        self.addPageTemplates(PageTemplate(id="handbook", frames=[frame], onPage=self._page))

    def _page(self, canvas, doc):
        page = canvas.getPageNumber()
        if page == 1:
            return
        canvas.saveState()
        canvas.setStrokeColor(LINE)
        canvas.line(18 * mm, 15 * mm, 192 * mm, 15 * mm)
        canvas.setFont(REGULAR, 7.5)
        canvas.setFillColor(GREY)
        canvas.drawString(18 * mm, 10.5 * mm, "Boforg Production Deployment Guide")
        canvas.drawRightString(192 * mm, 10.5 * mm, f"Page {page}")
        canvas.restoreState()

    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph):
            level = getattr(flowable, "toc_level", None)
            if level is not None:
                text = flowable.getPlainText()
                key = f"h-{self.page}-{abs(hash(text))}"
                self.canv.bookmarkPage(key)
                self.canv.addOutlineEntry(text, key, level=level, closed=False)
                self.notify("TOCEntry", (level, text, self.page, key))


styles = getSampleStyleSheet()
BODY = ParagraphStyle(
    "Body",
    parent=styles["BodyText"],
    fontName=REGULAR,
    fontSize=9.2,
    leading=13.2,
    textColor=INK,
    spaceAfter=5,
)
H1 = ParagraphStyle(
    "H1",
    parent=styles["Heading1"],
    fontName=BOLD,
    fontSize=19,
    leading=23,
    textColor=BLUE,
    spaceBefore=3,
    spaceAfter=10,
    keepWithNext=True,
)
H2 = ParagraphStyle(
    "H2",
    parent=styles["Heading2"],
    fontName=BOLD,
    fontSize=12.5,
    leading=16,
    textColor=INK,
    spaceBefore=10,
    spaceAfter=5,
    keepWithNext=True,
)
H3 = ParagraphStyle(
    "H3",
    parent=styles["Heading3"],
    fontName=BOLD,
    fontSize=10.2,
    leading=13,
    textColor=BLUE,
    spaceBefore=7,
    spaceAfter=4,
    keepWithNext=True,
)
LABEL = ParagraphStyle(
    "Label",
    parent=BODY,
    fontName=BOLD,
    fontSize=8.3,
    leading=11,
    textColor=GREY,
    spaceBefore=4,
    spaceAfter=2,
    keepWithNext=True,
)
LIST = ParagraphStyle(
    "List",
    parent=BODY,
    leftIndent=8 * mm,
    firstLineIndent=-4 * mm,
    bulletIndent=2.5 * mm,
    spaceAfter=3,
)
CODE = ParagraphStyle(
    "Code",
    fontName=MONO,
    fontSize=6.8,
    leading=9.2,
    textColor=colors.HexColor("#E8EEF8"),
    leftIndent=4 * mm,
    rightIndent=4 * mm,
    borderPadding=(7, 8, 7, 8),
    backColor=colors.HexColor("#142033"),
    borderColor=colors.HexColor("#2F405A"),
    borderWidth=0.5,
    borderRadius=3,
    spaceBefore=3,
    spaceAfter=7,
    splitLongWords=False,
)
SMALL = ParagraphStyle("Small", parent=BODY, fontSize=7.8, leading=10.5, textColor=GREY)


def inline_markup(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<font name='BoforgMono' color='#1459A6'>\1</font>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", escaped)
    return escaped


def callout(kind: str, text: str) -> Table:
    warning = kind == "WARNING"
    bg = PALE_RED if warning else PALE_GOLD if kind == "NOTE" else LIGHT_BLUE
    accent = RED if warning else GOLD if kind == "NOTE" else BLUE
    label = Paragraph(f"<b>{kind}</b>", ParagraphStyle("CalloutLabel", parent=BODY, textColor=accent, fontSize=8))
    body = Paragraph(inline_markup(text), ParagraphStyle("CalloutBody", parent=BODY, fontSize=8.4, leading=11.5))
    table = Table([[label, body]], colWidths=[22 * mm, 143 * mm], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("BOX", (0, 0), (-1, -1), 0.6, accent),
        ("LINEBEFORE", (0, 0), (0, -1), 3, accent),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return table


def parse_markdown(text: str):
    story = []
    lines = text.splitlines()
    paragraph: list[str] = []
    code: list[str] = []
    in_code = False

    def flush_paragraph():
        nonlocal paragraph
        if paragraph:
            story.append(Paragraph(inline_markup(" ".join(p.strip() for p in paragraph)), BODY))
            paragraph = []

    for line in lines:
        if line.startswith("```"):
            flush_paragraph()
            if in_code:
                story.append(Preformatted("\n".join(code), CODE, maxLineLength=108))
                code = []
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code.append(line)
            continue
        if line == "---PAGE---":
            flush_paragraph()
            story.append(PageBreak())
            continue
        if not line.strip():
            flush_paragraph()
            continue
        heading = re.match(r"^(#{1,3})\s+(.+)$", line)
        if heading:
            flush_paragraph()
            level = len(heading.group(1)) - 1
            style = (H1, H2, H3)[level]
            p = Paragraph(inline_markup(heading.group(2)), style)
            p.toc_level = level
            story.append(p)
            continue
        special = re.match(r"^(NOTE|WARNING|DECISION):\s*(.+)$", line)
        if special:
            flush_paragraph()
            story.append(callout(special.group(1), special.group(2)))
            story.append(Spacer(1, 4))
            continue
        if line in {"Command", "Purpose", "Expected Result", "File", "Verification"}:
            flush_paragraph()
            story.append(Paragraph(line.upper(), LABEL))
            continue
        check = re.match(r"^- \[([ xX])\]\s+(.+)$", line)
        if check:
            flush_paragraph()
            symbol = "[x]" if check.group(1).lower() == "x" else "[ ]"
            story.append(Paragraph(f"{symbol}&nbsp;&nbsp;{inline_markup(check.group(2))}", LIST))
            continue
        bullet = re.match(r"^-\s+(.+)$", line)
        if bullet:
            flush_paragraph()
            story.append(Paragraph(f"•&nbsp;&nbsp;{inline_markup(bullet.group(1))}", LIST))
            continue
        numbered = re.match(r"^(\d+)\.\s+(.+)$", line)
        if numbered:
            flush_paragraph()
            story.append(Paragraph(f"{numbered.group(1)}.&nbsp;&nbsp;{inline_markup(numbered.group(2))}", LIST))
            continue
        paragraph.append(line)
    flush_paragraph()
    return story


def cover_story():
    title = ParagraphStyle("CoverTitle", fontName=BOLD, fontSize=27, leading=33, textColor=colors.white, alignment=TA_LEFT)
    subtitle = ParagraphStyle("CoverSubtitle", fontName=REGULAR, fontSize=16, leading=21, textColor=colors.HexColor("#C7D9F1"))
    kicker = ParagraphStyle("Kicker", fontName=BOLD, fontSize=9, leading=12, textColor=GOLD, spaceAfter=10)
    meta = ParagraphStyle("Meta", fontName=REGULAR, fontSize=9, leading=14, textColor=colors.white)
    panel = Table([
        [Paragraph("PRODUCTION HANDBOOK", kicker)],
        [Paragraph("Boforg Production<br/>Deployment Guide", title)],
        [Spacer(1, 9 * mm)],
        [Paragraph("Multi-Subdomain Django Deployment", subtitle)],
        [Spacer(1, 52 * mm)],
        [Paragraph("Generated 8 August 2026<br/>Ubuntu 24.04 LTS · Hostinger VPS · 72.60.20.46", meta)],
    ], colWidths=[174 * mm], rowHeights=None)
    panel.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), INK),
        ("LEFTPADDING", (0, 0), (-1, -1), 16 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 16 * mm),
        ("TOPPADDING", (0, 0), (-1, 0), 17 * mm),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 16 * mm),
    ]))
    return [panel, PageBreak()]


def toc_story():
    title = Paragraph("Table of Contents", H1)
    toc = TableOfContents()
    toc.levelStyles = [
        ParagraphStyle("TOC1", fontName=BOLD, fontSize=10, leading=14, textColor=BLUE, leftIndent=0, firstLineIndent=0, spaceBefore=5),
        ParagraphStyle("TOC2", fontName=REGULAR, fontSize=8.5, leading=11, textColor=INK, leftIndent=7 * mm, firstLineIndent=0),
        ParagraphStyle("TOC3", fontName=REGULAR, fontSize=7.5, leading=10, textColor=GREY, leftIndent=14 * mm, firstLineIndent=0),
    ]
    return [title, toc, PageBreak()]


def main():
    doc = HandbookDocTemplate(str(OUTPUT))
    story = cover_story() + toc_story() + parse_markdown(SOURCE.read_text(encoding="utf-8"))
    doc.multiBuild(story)
    print(OUTPUT)


if __name__ == "__main__":
    main()

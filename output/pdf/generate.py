"""Regenerate example assets from the project root with ReportLab installed."""

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph
from xml.sax.saxutils import escape

from portal.demo.catalog import COMPANY, SAMPLES

OUT = Path(__file__).resolve().parent
INK = colors.HexColor("#132a42")
ACCENT = colors.HexColor("#076e65")
MUTED = colors.HexColor("#4c6275")
WIDTH, HEIGHT = A4


def generate(sample):
    pdf = canvas.Canvas(str(OUT / f"{sample.slug}.pdf"), pagesize=A4, invariant=True)
    pdf.setTitle(sample.title)
    pdf.setAuthor(COMPANY + " (fictional)")
    pdf.setFillColor(INK)
    pdf.rect(0, HEIGHT - 116, WIDTH, 116, fill=1, stroke=0)
    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(48, HEIGHT - 42, COMPANY.upper())
    pdf.setFont("Helvetica", 10)
    pdf.drawString(48, HEIGHT - 63, sample.category.upper())
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(48, HEIGHT - 92, "FICTIONAL SAMPLE  /  NOT VALID AS EVIDENCE")

    pdf.setFillColor(INK)
    pdf.setFont("Helvetica-Bold", 22)
    pdf.drawString(48, HEIGHT - 158, sample.title)
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 10)
    pdf.drawString(48, HEIGHT - 181, sample.reference)
    y = HEIGHT - 220
    style = ParagraphStyle("body", fontName="Helvetica", fontSize=11, leading=16, textColor=INK)
    for heading, body in sample.sections:
        pdf.setFillColor(ACCENT)
        pdf.setFont("Helvetica-Bold", 12)
        pdf.drawString(48, y, heading)
        paragraph = Paragraph(escape(body), style)
        _, height = paragraph.wrap(WIDTH - 96, 700)
        paragraph.drawOn(pdf, 48, y - height - 7)
        y -= height + 30
    if y < 84:
        raise ValueError(f"{sample.slug} exceeds one page")
    pdf.setStrokeColor(colors.HexColor("#b9cccf"))
    pdf.line(48, 67, WIDTH - 48, 67)
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 9)
    pdf.drawString(48, 47, "Example content for the Trust Centre demo")
    pdf.drawRightString(WIDTH - 48, 47, "1 / 1")
    pdf.save()


for entry in SAMPLES:
    generate(entry)

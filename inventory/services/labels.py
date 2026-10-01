"""Monochrome labels with scan-safe margins and A4 cutting guides."""
from io import BytesIO
from reportlab.pdfgen import canvas
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.lib.units import mm
from reportlab.graphics import renderPDF
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.barcode import code128, qr


def fitted_text(text, width, font="Helvetica", size=7):
    text = str(text or "")
    if stringWidth(text, font, size) <= width:
        return text
    while text and stringWidth(text + "...", font, size) > width:
        text = text[:-1]
    return text.rstrip() + "..."


def product_lines(name, width):
    words = str(name).split()
    first = ""
    while words and stringWidth((first + " " + words[0]).strip(), "Helvetica-Bold", 9) <= width:
        first = (first + " " + words.pop(0)).strip()
    if not first and words:
        first = fitted_text(words.pop(0), width, "Helvetica-Bold", 9)
    return [first, fitted_text(" ".join(words), width, "Helvetica-Bold", 9)]


def draw_label(pdf, label):
    # Content is inset from a 94 x 43 mm cutting boundary; no ink enters
    # either barcode's quiet zone. Black/white also suits thermal printers.
    pdf.setFillGray(0)
    pdf.setFont("Helvetica-Bold", 8)
    pdf.drawString(3 * mm, 38.5 * mm, "BOFORG TECHNOLOGIES")
    pdf.setFont("Helvetica", 6)
    pdf.drawRightString(91 * mm, 38.5 * mm, "PRODUCT ID" if label.get("kind") == "product" else "UNIT ID")
    pdf.setStrokeGray(.7)
    pdf.setLineWidth(.35)
    pdf.line(3 * mm, 36.5 * mm, 91 * mm, 36.5 * mm)
    pdf.setFont("Helvetica-Bold", 9)
    for index, text in enumerate(product_lines(label["name"], 88 * mm)):
        pdf.drawString(3 * mm, (32 - index * 3.7) * mm, text)
    area_x, area_width = 2 * mm, 64 * mm
    barcode = code128.Code128(label["code"], barHeight=12 * mm, barWidth=.28 * mm, quiet=True)
    scale = min(1, area_width / barcode.width)
    pdf.saveState()
    pdf.translate(area_x + (area_width - barcode.width * scale) / 2, 11 * mm)
    pdf.scale(scale, 1)
    barcode.drawOn(pdf, 0, 0)
    pdf.restoreState()
    font_size = min(9, 9 * area_width / stringWidth(label["code"], "Helvetica-Bold", 9))
    pdf.setFont("Helvetica-Bold", font_size)
    pdf.drawCentredString(area_x + area_width / 2, 7 * mm, label["code"])
    widget = qr.QrCodeWidget(label["code"])
    bounds = widget.getBounds()
    size = 23 * mm
    drawing = Drawing(size, size, transform=[size/(bounds[2]-bounds[0]), 0, 0, size/(bounds[3]-bounds[1]), 0, 0])
    drawing.add(widget)
    renderPDF.draw(drawing, pdf, 69 * mm, 7 * mm)
    pdf.setFillGray(.25)
    pdf.setFont("Helvetica", 6)
    detail = "S/N: " + label["serial"] if label.get("serial") else "Boforg inventory"
    pdf.drawString(3 * mm, 3 * mm, fitted_text(detail, 64 * mm, size=6))
    pdf.drawRightString(91 * mm, 3 * mm, "boforg.co.zw")


def render_labels(labels, *, thermal=False):
    output = BytesIO()
    width, height = (100 * mm, 50 * mm) if thermal else (210 * mm, 297 * mm)
    pdf = canvas.Canvas(output, pagesize=(width, height))
    pdf.setTitle("Boforg inventory labels")
    per_page = 1 if thermal else 12
    for start in range(0, len(labels), per_page):
        if start:
            pdf.showPage()
        if not thermal:
            pdf.setFillGray(.35)
            pdf.setFont("Helvetica", 7)
            pdf.drawString(10 * mm, 7 * mm, "Print at 100% / actual size. Cut on the dashed outlines. Stickers: 94 x 43 mm.")
            pdf.drawRightString(201 * mm, 7 * mm, f"Sheet {start // 12 + 1} / {(len(labels) + 11) // 12}")
        for position, label in enumerate(labels[start:start + per_page]):
            x = 3 * mm if thermal else (10 + position % 2 * 97) * mm
            y = 3.5 * mm if thermal else height - (10 + (position // 2 + 1) * 45) * mm
            pdf.saveState()
            pdf.translate(x, y)
            pdf.setLineWidth(.4)
            pdf.setStrokeGray(.65)
            if not thermal:
                pdf.setDash(2, 2)
                pdf.rect(0, 0, 94 * mm, 43 * mm)
                pdf.setDash()
            draw_label(pdf, label)
            pdf.restoreState()
    pdf.save()
    return output.getvalue()

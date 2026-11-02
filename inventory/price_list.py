"""Customer-facing prices: intentionally excludes costs and supplier information."""
from io import BytesIO
from xml.sax.saxutils import escape

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, LongTable, TableStyle


def build_price_list(products, search='', low=False):
    buffer = BytesIO()
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle('ProductCell', fontName='Helvetica', fontSize=9, leading=12, textColor=colors.HexColor('#18354f'), wordWrap='CJK'))
    styles.add(ParagraphStyle('PriceCell', parent=styles['ProductCell'], alignment=TA_RIGHT))
    text = lambda value, style='ProductCell': Paragraph(escape(str(value or '')), styles[style])
    date = timezone.localdate().strftime('%d %B %Y')
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=16*mm, leftMargin=16*mm, topMargin=18*mm, bottomMargin=18*mm, title='Boforg product price list', author='Boforg Technologies')
    story = [Paragraph('BOFORG TECHNOLOGIES', styles['Title']), Paragraph('Product price list', styles['Heading2']), text(f'Updated {date} | sales@boforg.co.zw | +263 786 264 994'), Spacer(1, 5*mm)]
    if search:
        story.extend([text(f'Search: {search}'), Spacer(1, 3*mm)])
    if low:
        story.extend([text('Selection: low stock products'), Spacer(1, 3*mm)])
    rows = [[text('Product / SKU'), text('Category'), text('Availability'), text('Price before tax', 'PriceCell')]]
    count = 0
    for product in products.iterator(chunk_size=500):
        count += 1
        availability = 'Service' if not product.track_inventory else ('In stock' if product.available_stock > 0 else 'On request')
        rows.append([
            Paragraph(f'<b>{escape(product.name)}</b><br/><font size="8" color="#60758a">{escape(product.sku)}</font>', styles['ProductCell']),
            text(product.category.name if product.category else 'General'), text(availability),
            text(f'{product.currency} {product.price:,.2f}', 'PriceCell'),
        ])
    if count:
        table = LongTable(rows, colWidths=[79*mm, 40*mm, 28*mm, 31*mm], repeatRows=1, hAlign='LEFT')
        table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#e6eff8')),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f6f9fc')]),
            ('VALIGN', (0,0), (-1,-1), 'TOP'), ('LEFTPADDING',(0,0),(-1,-1),8),
            ('RIGHTPADDING',(0,0),(-1,-1),8), ('TOPPADDING',(0,0),(-1,-1),9), ('BOTTOMPADDING',(0,0),(-1,-1),9),
            ('LINEBELOW',(0,0),(-1,0),1,colors.HexColor('#a0b7cd')),
            ('LINEBELOW',(0,1),(-1,-1),.3,colors.HexColor('#dce5ed')),
        ]))
        story.append(table)
    else:
        story.append(text('No active products match this selection.'))
    story.extend([Spacer(1, 5*mm), text(f'{count} products. Prices exclude tax and are listed in each product currency. Applicable tax is added on invoices and quotations. Availability and prices may change; contact us to confirm your order.')])

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont('Helvetica', 8)
        canvas.setFillColor(colors.HexColor('#60758a'))
        canvas.drawString(16*mm, 10*mm, f'Boforg Technologies | {date}')
        canvas.drawRightString(A4[0]-16*mm, 10*mm, f'Page {document.page}')
        canvas.restoreState()
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()

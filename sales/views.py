from decimal import Decimal

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.db.models import Q

from .forms import (
    QuotationForm,
    InvoiceForm,
    DocumentLineForm,
    PaymentForm,
    ComboSelectionForm,
)
from .models import (
    Quotation,
    Invoice,
    Payment,
    DocumentLine,
)
from .services import PricingService, StockService
from inventory.models import Combo
from inventory.services.combos import (
    add_combo_to_invoice,
    add_combo_to_quotation,
    combo_available_quantity,
)


@login_required
def sales_home(request):
    q = request.GET.get('q', '')
    inv_qs = Invoice.objects.filter(
        Q(number__icontains=q) |
        Q(customer__name__icontains=q) |
        Q(status__icontains=q)
    ).select_related('customer')
    quo_qs = Quotation.objects.filter(
        Q(number__icontains=q) |
        Q(customer__name__icontains=q) |
        Q(status__icontains=q)
    ).select_related('customer')

    qp = request.GET.get('q_page')
    ip = request.GET.get('i_page')
    quo_pg = Paginator(quo_qs.order_by('-date', '-id'), 10).get_page(qp)
    inv_pg = Paginator(inv_qs.order_by('-date', '-id'), 10).get_page(ip)
    return render(request, 'sales/home.html', {'invoices': inv_pg, 'quotations': quo_pg, 'q': q})


@login_required
def quotation_create(request):
    form = QuotationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        quotation = form.save()
        return redirect('ims:sales:quotation_edit', quotation.id)
    return render(request, 'sales/quotation_form.html', {'form': form})


@login_required
def quotation_edit(request, pk):
    quotation = get_object_or_404(Quotation, pk=pk)
    line_form = DocumentLineForm(request.POST or None)
    combo_form = ComboSelectionForm(request.POST or None, prefix='combo')

    if request.method == 'POST':
        if 'add_line' in request.POST and line_form.is_valid():
            line = line_form.save(commit=False)
            product = line.product
            if product:
                pricing = PricingService.apply_best_rule(product, int(line.quantity), Decimal(str(line.unit_price)))
                if pricing.discount_percent:
                    factor = Decimal('1') - (pricing.discount_percent / Decimal('100'))
                    line.unit_price = (Decimal(str(line.unit_price)) * factor).quantize(Decimal('0.01'))
                elif pricing.discount_value:
                    line.unit_price = max(
                        Decimal('0.00'),
                        Decimal(str(line.unit_price)) - pricing.discount_value,
                    ).quantize(Decimal('0.01'))
                if not line.description:
                    line.description = product.name
            line.line_total = (Decimal(str(line.unit_price)) * Decimal(str(line.quantity))).quantize(Decimal('0.01'))
            line.quotation = quotation
            line.save()
            return redirect('ims:sales:quotation_edit', pk)
        if 'add_combo' in request.POST:
            combo_form = ComboSelectionForm(request.POST, prefix='combo')
            if combo_form.is_valid():
                combo = combo_form.cleaned_data['combo']
                quantity = combo_form.cleaned_data['quantity']
                try:
                    add_combo_to_quotation(quotation, combo.id, quantity)
                except ValueError as exc:
                    messages.error(request, str(exc))
                return redirect('ims:sales:quotation_edit', pk)

    combo_options = []
    for combo in Combo.objects.filter(is_active=True).prefetch_related('items__product'):
        components = [
            {
                'product': item.product,
                'quantity': item.quantity,
            }
            for item in combo.items.all()
        ]
        combo_options.append({
            'combo': combo,
            'price': combo.compute_price(),
            'available': combo_available_quantity(combo),
            'components': components,
        })

    lines = list(quotation.lines.select_related('product').order_by('id'))
    return render(
        request,
        'sales/quotation_detail.html',
        {
            'quotation': quotation,
            'item_form': line_form,
            'combo_form': combo_form,
            'combo_options': combo_options,
            'lines': lines,
        },
    )


@login_required
def quotation_to_invoice(request, pk):
    quotation = get_object_or_404(Quotation, pk=pk)
    invoice = Invoice.objects.create(customer=quotation.customer, quotation=quotation, notes=quotation.notes)
    for line in quotation.lines.all():
        DocumentLine.objects.create(
            invoice=invoice,
            product=line.product,
            description=line.description,
            quantity=line.quantity,
            unit_price=line.unit_price,
            tax_rate_percent=line.tax_rate_percent,
            line_total=line.line_total,
        )
    quotation.status = Quotation.CONVERTED
    quotation.save(update_fields=['status'])
    try:
        StockService.reserve_stock(invoice)
    except Exception as exc:
        messages.error(request, f"Stock reservation failed on conversion: {exc}")
    return redirect('ims:sales:invoice_edit', invoice.id)


@login_required
def quotation_pdf(request, pk):
    from .pdf_utils import render_pdf_from_html

    quotation = get_object_or_404(Quotation, pk=pk)
    lines = list(quotation.lines.select_related('product').order_by('id'))
    html = render_to_string('sales/pdf_quotation.html', {'q': quotation, 'lines': lines})
    pdf = render_pdf_from_html(html, base_url=request.build_absolute_uri())
    response = HttpResponse(pdf, content_type='application/pdf')
    disposition = 'inline' if (request.GET.get('preview') or request.GET.get('disposition') == 'inline') else 'attachment'
    response['Content-Disposition'] = f"{disposition}; filename=\"{quotation.number}.pdf\""
    return response



@login_required
def invoice_create(request):
    form = InvoiceForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        invoice = form.save()
        return redirect('ims:sales:invoice_edit', invoice.id)
    return render(request, 'sales/invoice_form.html', {'form': form})


@login_required
def invoice_edit(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    line_form = DocumentLineForm(request.POST or None)
    combo_form = ComboSelectionForm(request.POST or None, prefix='combo')
    pay_form = PaymentForm(request.POST or None, initial={'invoice': invoice})

    if request.method == 'POST':
        if 'add_line' in request.POST and line_form.is_valid():
            line = line_form.save(commit=False)
            product = line.product
            if product:
                pricing = PricingService.apply_best_rule(product, int(line.quantity), Decimal(str(line.unit_price)))
                if pricing.discount_percent:
                    factor = Decimal('1') - (pricing.discount_percent / Decimal('100'))
                    line.unit_price = (Decimal(str(line.unit_price)) * factor).quantize(Decimal('0.01'))
                elif pricing.discount_value:
                    line.unit_price = max(
                        Decimal('0.00'),
                        Decimal(str(line.unit_price)) - pricing.discount_value,
                    ).quantize(Decimal('0.01'))
                if not line.description:
                    line.description = product.name
            line.line_total = (Decimal(str(line.unit_price)) * Decimal(str(line.quantity))).quantize(Decimal('0.01'))
            line.invoice = invoice
            line.save()
            try:
                StockService.reserve_stock(invoice)
            except Exception as exc:
                messages.error(request, f"Stock reservation failed: {exc}")
            return redirect('ims:sales:invoice_edit', pk)
        if 'add_combo' in request.POST:
            combo_form = ComboSelectionForm(request.POST, prefix='combo')
            if combo_form.is_valid():
                combo = combo_form.cleaned_data['combo']
                quantity = combo_form.cleaned_data['quantity']
                try:
                    add_combo_to_invoice(invoice, combo.id, quantity)
                    StockService.reserve_stock(invoice)
                except ValueError as exc:
                    messages.error(request, str(exc))
                except Exception as exc:
                    messages.error(request, f"Stock reservation failed: {exc}")
                return redirect('ims:sales:invoice_edit', pk)
        if 'add_payment' in request.POST and pay_form.is_valid():
            payment = pay_form.save()
            paid = StockService.amount_paid(invoice)
            if paid >= invoice.total:
                try:
                    invoice.confirm(user=request.user)
                    invoice.status = Invoice.PAID
                    invoice.save(update_fields=['status'])
                    StockService.finalize_sale(invoice)
                except Exception as exc:
                    messages.error(request, f"Invoice confirmation failed: {exc}")
            elif paid > 0 and invoice.status != Invoice.PAID:
                invoice.status = Invoice.PENDING
                invoice.save(update_fields=['status'])
            return redirect('ims:sales:invoice_edit', pk)

    combo_options = []
    for combo in Combo.objects.filter(is_active=True).prefetch_related('items__product'):
        components = [
            {
                'product': item.product,
                'quantity': item.quantity,
            }
            for item in combo.items.all()
        ]
        combo_options.append({
            'combo': combo,
            'price': combo.compute_price(),
            'available': combo_available_quantity(combo),
            'components': components,
        })

    lines = list(invoice.lines.select_related('product').order_by('id'))
    return render(
        request,
        'sales/invoice_detail.html',
        {
            'invoice': invoice,
            'item_form': line_form,
            'combo_form': combo_form,
            'combo_options': combo_options,
            'pay_form': pay_form,
            'lines': lines,
        },
    )


@login_required
def invoice_pdf(request, pk):
    from .pdf_utils import render_pdf_from_html

    invoice = get_object_or_404(Invoice, pk=pk)
    lines = list(invoice.lines.select_related('product').order_by('id'))
    html = render_to_string('sales/pdf_invoice.html', {'inv': invoice, 'lines': lines})
    pdf = render_pdf_from_html(html, base_url=request.build_absolute_uri())
    response = HttpResponse(pdf, content_type='application/pdf')
    disposition = 'inline' if (request.GET.get('preview') or request.GET.get('disposition') == 'inline') else 'attachment'
    response['Content-Disposition'] = f"{disposition}; filename=\"{invoice.number}.pdf\""
    return response




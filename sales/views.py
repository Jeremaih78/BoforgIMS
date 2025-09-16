from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.db.models import Q, Sum
from django.http import HttpResponse
from django.contrib import messages

from django.template.loader import render_to_string
from .models import Quotation, QuotationItem, Invoice, InvoiceItem, Payment
from .forms import QuotationForm, QuotationItemForm, InvoiceForm, InvoiceItemForm, PaymentForm
from .services import PricingService, StockService

@login_required
def sales_home(request):
    q = request.GET.get('q','')
    inv_qs = Invoice.objects.filter(
        Q(number__icontains=q)|Q(customer__name__icontains=q)|Q(status__icontains=q)
    ).select_related('customer')
    quo_qs = Quotation.objects.filter(
        Q(number__icontains=q)|Q(customer__name__icontains=q)|Q(status__icontains=q)
    ).select_related('customer')
    from django.core.paginator import Paginator
    qp = request.GET.get('q_page')
    ip = request.GET.get('i_page')
    quo_pg = Paginator(quo_qs.order_by('-date','-id'), 10).get_page(qp)
    inv_pg = Paginator(inv_qs.order_by('-date','-id'), 10).get_page(ip)
    return render(request,'sales/home.html',{'invoices':inv_pg,'quotations':quo_pg,'q':q})

@login_required
def quotation_create(request):
    form = QuotationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        quotation = form.save()
        return redirect('quotation_edit', quotation.id)
    return render(request,'sales/quotation_form.html',{'form':form})

@login_required
def quotation_edit(request, pk):
    quotation = get_object_or_404(Quotation, pk=pk)
    item_form = QuotationItemForm(request.POST or None)
    if request.method == 'POST' and item_form.is_valid():
        item = item_form.save(commit=False)
        # Auto-fill unit price from product if not provided
        if item.product and (item.unit_price is None or item.unit_price == 0):
            item.unit_price = item.product.price
        # Apply best price rule if no manual discount
        if item.product and not item.discount_percent and not item.discount_value:
            pr = PricingService.apply_best_rule(item.product, item.quantity, item.unit_price)
            item.discount_percent = pr.discount_percent
            item.discount_value = pr.discount_value
        # Default tax from product
        if item.product:
            item.tax_rate = item.product.tax_rate
        item.quotation = quotation
        item.save()
        return redirect('quotation_edit', pk)
    return render(request,'sales/quotation_detail.html',{'quotation':quotation,'item_form':item_form})

@login_required
def quotation_to_invoice(request, pk):
    q = get_object_or_404(Quotation, pk=pk)
    inv = Invoice.objects.create(customer=q.customer, quotation=q, notes=q.notes)
    for it in q.items.all():
        InvoiceItem.objects.create(invoice=inv, product=it.product, description=it.description, quantity=it.quantity, unit_price=it.unit_price)
    q.status = Quotation.CONVERTED; q.save()
    # Create initial stock reservations for converted invoice so finalization can deduct
    try:
        StockService.reserve_stock(inv)
    except Exception as e:
        messages.error(request, f"Stock reservation failed on conversion: {e}")
    return redirect('invoice_edit', inv.id)

@login_required
def quotation_pdf(request, pk):
    from .pdf_utils import render_pdf_from_html
    quotation = get_object_or_404(Quotation, pk=pk)
    html = render_to_string('sales/pdf_quotation.html', {'q':quotation})
    pdf = render_pdf_from_html(html, base_url=request.build_absolute_uri())
    response = HttpResponse(pdf, content_type='application/pdf')
    disposition = 'inline' if (request.GET.get('preview') or request.GET.get('disposition') == 'inline') else 'attachment'
    response['Content-Disposition'] = f'{disposition}; filename="{quotation.number}.pdf"'
    return response

@login_required
def invoice_create(request):
    form = InvoiceForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        inv = form.save()
        return redirect('invoice_edit', inv.id)
    return render(request,'sales/invoice_form.html',{'form':form})

@login_required
def invoice_edit(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    item_form = InvoiceItemForm(request.POST or None)
    pay_form = PaymentForm(request.POST or None, initial={'invoice':invoice})
    if request.method == 'POST':
        if 'add_item' in request.POST and item_form.is_valid():
            it = item_form.save(commit=False)
            if it.product and (it.unit_price is None or it.unit_price == 0):
                it.unit_price = it.product.price
            # Apply best rule when discount not specified
            if it.product and not it.discount_percent and not it.discount_value:
                pr = PricingService.apply_best_rule(it.product, it.quantity, it.unit_price)
                it.discount_percent = pr.discount_percent
                it.discount_value = pr.discount_value
            if it.product:
                it.tax_rate = it.product.tax_rate
            it.invoice = invoice
            it.save()
            # Ensure reservation exists for this invoice based on current items
            try:
                StockService.reserve_stock(invoice)
            except Exception as e:
                messages.error(request, f"Stock reservation failed: {e}")
            return redirect('invoice_edit', pk)
        if 'add_payment' in request.POST and pay_form.is_valid():
            pay_form.save()
            # update status
            paid = StockService.amount_paid(invoice)
            if paid >= invoice.total:
                # Ensure reservations exist before finalizing (covers converted invoices)
                if not invoice.reservations.exists():
                    try:
                        StockService.reserve_stock(invoice)
                    except Exception as e:
                        messages.error(request, f"Stock reservation failed: {e}")
                invoice.status = Invoice.PAID
                invoice.save(update_fields=['status'])
                # finalize stock
                StockService.finalize_sale(invoice)
            elif paid > 0:
                # keep as pending to denote partial payment in current model
                if invoice.status != Invoice.PAID:
                    invoice.status = Invoice.PENDING
                    invoice.save(update_fields=['status'])
                try:
                    StockService.reserve_stock(invoice)
                except Exception as e:
                    messages.error(request, f"Stock reservation failed: {e}")
            return redirect('invoice_edit', pk)
    return render(request,'sales/invoice_detail.html',{'invoice':invoice,'item_form':item_form,'pay_form':pay_form})

@login_required
def invoice_pdf(request, pk):
    from .pdf_utils import render_pdf_from_html
    invoice = get_object_or_404(Invoice, pk=pk)
    html = render_to_string('sales/pdf_invoice.html', {'inv':invoice})
    pdf = render_pdf_from_html(html, base_url=request.build_absolute_uri())
    response = HttpResponse(pdf, content_type='application/pdf')
    disposition = 'inline' if (request.GET.get('preview') or request.GET.get('disposition') == 'inline') else 'attachment'
    response['Content-Disposition'] = f'{disposition}; filename="{invoice.number}.pdf"'
    return response

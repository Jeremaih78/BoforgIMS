"""Single-submit sales authoring, using the existing reservation service."""
from decimal import Decimal
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .forms import ComposerComboFormSet, DocumentLineFormSet, InvoiceForm, QuotationForm
from .models import DocumentLine, Invoice, Quotation
from .services import StockService
from inventory.models import Combo
from inventory.services.combos import add_combo_to_document


def compose(request, kind, pk=None):
    model, form_class = (Invoice, InvoiceForm) if kind == 'invoice' else (Quotation, QuotationForm)
    document = get_object_or_404(model, pk=pk) if pk else None
    if document and request.method == 'POST':
        # The header lock serializes this path with payments, scanning and edits.
        document = model.objects.select_for_update().get(pk=pk)
    locked = bool(document and (
        (kind == 'quotation' and (document.status == Quotation.CONVERTED or document.invoice_set.exists())) or
        (kind == 'invoice' and (document.stock_finalized or document.status == Invoice.PAID or document.payments.exists()))
    ))
    if locked:
        messages.error(request, 'This document is locked because it is converted, paid or finalized.')
        return redirect(f'ims:sales:{kind}_edit', pk)

    data = request.POST.copy() if request.method == 'POST' else None
    if data is not None and not any(key.startswith('combos-') for key in data):
        # Previously released product-only clients have no combo management fields.
        data.update({'combos-TOTAL_FORMS': '0', 'combos-INITIAL_FORMS': '0'})
    form = form_class(data, instance=document)
    queryset = document.lines.filter(product__isnull=False, combo__isnull=True).order_by('pk') if document else DocumentLine.objects.none()
    line_set = DocumentLineFormSet(data, queryset=queryset, prefix='lines')
    combo_set = ComposerComboFormSet(data, prefix='combos')
    fixed_lines = list(document.lines.exclude(pk__in=queryset.values('pk'))) if document else []
    if request.method == 'POST':
        valid_header = form.is_valid()
        valid_lines = line_set.is_valid()
        valid_combos = combo_set.is_valid()
        # Every existing editable line must be represented, including DELETE rows.
        expected = set(queryset.values_list('pk', flat=True))
        submitted = {f.instance.pk for f in line_set.initial_forms if f.instance.pk}
        if expected != submitted:
            form.add_error(None, 'The document lines changed. Reload the editor before saving.')
            valid_header = False
        if valid_lines and valid_combos:
            product_forms = [f for f in line_set.forms if f.cleaned_data.get('product') and not f.cleaned_data.get('DELETE')]
            combo_forms = [f for f in combo_set.forms if f.cleaned_data.get('combo') and not f.cleaned_data.get('DELETE')]
            if not product_forms and not combo_forms:
                form.add_error(None, 'Add at least one product or combo before saving.')
                valid_header = False
            currencies = {f.cleaned_data['product'].currency for f in product_forms}
            currencies.update(f.cleaned_data['combo_info']['currency'] for f in combo_forms)
            currencies.update(line.product.currency for line in fixed_lines if line.product_id)
            if len(currencies) > 1:
                form.add_error(None, 'Use products and combos in one currency per document. Currency conversion is not automatic.')
                valid_header = False
            if len(product_forms) + len(combo_forms) > 100:
                form.add_error(None, 'Use up to 100 product and combo rows per save.')
                valid_header = False
            expanded_count = len(product_forms) + len(fixed_lines) + sum(
                len(f.cleaned_data['combo'].items.all()) + (Decimal(f.cleaned_data['combo_info']['discount']) > 0)
                for f in combo_forms
            )
            if expanded_count > 100:
                form.add_error(None, 'Keep the document within 100 expanded lines, including combo components and discounts.')
                valid_header = False
        if valid_header and valid_lines and valid_combos:
            try:
                with transaction.atomic():
                    saved = form.save(commit=False)
                    if kind == 'invoice' and not saved.created_by_id:
                        saved.created_by = request.user
                    saved.save()
                    for line in line_set.save(commit=False):
                        setattr(line, kind, saved)
                        if not line.description:
                            line.description = line.product.name
                        line.save()
                    for line in line_set.deleted_objects:
                        line.delete()
                    for combo_form in combo_forms:
                        add_combo_to_document(saved, combo_form.cleaned_data['combo'].pk, combo_form.cleaned_data['quantity'])
                    if kind == 'invoice':
                        StockService.reserve_stock(saved)
            except Combo.DoesNotExist:
                form.add_error(None, 'A selected combo is no longer available. Search for it again before saving.')
            except (ValueError, ValidationError) as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, f'{kind.title()} saved.')
                target = reverse(f'ims:sales:{kind}_edit', args=[saved.pk])
                if request.POST.get('save_action') == 'pdf':
                    target += '?download=1'
                return redirect(target)
    return render(request, 'sales/document_composer.html', {
        'form': form, 'line_set': line_set, 'combo_set': combo_set, 'kind': kind, 'document': document,
        'fixed_lines': fixed_lines,
        'fixed_total': sum(line.line_total for line in fixed_lines),
    })

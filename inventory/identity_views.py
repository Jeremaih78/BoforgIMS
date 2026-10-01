import csv
from urllib.parse import urlencode
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q, Count, F
from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from core.permissions import ims_permission, allowed
from inventory.models import Product, ProductUnit, ProductBarcode, Stocktake, ServiceCase, UnitEvent
from inventory.identity_forms import BarcodeForm, UnitActionForm, StocktakeForm, CaseForm, CaseUpdateForm
from inventory.services import identity, stocktake, after_sales


def json_errors(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except (ValidationError, ValueError) as exc:
            data = {'error': '; '.join(exc.messages) if isinstance(exc, ValidationError) else str(exc)}
            request = args[0]
            if isinstance(exc, identity.UnknownIdentifier) and allowed(request.user, 'inventory.change_product'):
                data['assign_url'] = reverse('ims:inventory:assign_barcode') + '?' + urlencode({'code':request.POST.get('code', '')})
            return JsonResponse(data, status=400)
        except IntegrityError:
            return JsonResponse({'error': 'Identifier or request already used. Refresh the record before retrying.'}, status=409)
    return wrapped


def can_manage(user):
    return allowed(user, 'inventory.manage_unit_lifecycle')


def filtered_units(params):
    units = ProductUnit.objects.select_related('product', 'shipment__supplier')
    q = params.get('q', '').strip()
    if q:
        units = units.filter(Q(unit_id__iexact=q) | Q(serial_number__iexact=q) | Q(product__name__icontains=q) | Q(product__sku__icontains=q))
    state = params.get('status', '')
    if state:
        units = units.filter(status=state)
    if params.get('location'):
        units = units.filter(location=params['location'])
    if params.get('shipment'):
        try:
            units = units.filter(shipment_id=int(params['shipment']))
        except ValueError:
            units = units.none()
    return units.order_by('product__name', 'pk')


@login_required
@ims_permission('inventory.view_productunit', staff=True)
def operations(request):
    units = filtered_units(request.GET)
    q = request.GET.get('q', '').strip()
    state = request.GET.get('status', '')
    if request.GET.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="serialized-inventory.csv"'
        writer = csv.writer(response)
        writer.writerow(['Unit ID', 'Product', 'Manufacturer serial', 'Status', 'Location', 'Received'])
        for unit in units.iterator():
            writer.writerow([csv_safe(v) for v in (unit.unit_id, unit.product.name, unit.serial_number, unit.status, unit.location, unit.created_at.date())])
        return response
    return render(request, 'inventory/identity/operations.html', {
        'page_obj': Paginator(units, 30).get_page(request.GET.get('page')), 'q': q, 'state': state,
        'statuses': ProductUnit.STATUS_CHOICES, 'stats': ProductUnit.objects.values('status').annotate(total=Count('pk')),
        'open_cases': ServiceCase.objects.exclude(status='CLOSED').count(),
        'can_manage': can_manage(request.user), 'can_labels': allowed(request.user, 'inventory.print_inventory_labels'),
        'can_count': allowed(request.user, 'inventory.add_stocktake', staff=True),
    })


def csv_safe(value):
    value = str(value or '')
    return "'" + value if value.startswith(('=', '+', '-', '@', '\t', '\r')) else value


@login_required
@ims_permission('inventory.change_product')
def assign_unknown(request):
    from django import forms
    form = BarcodeForm(request.POST or None, initial={'code':request.GET.get('code', '')})
    form.fields['product'].widget = forms.HiddenInput()
    if request.method == 'POST' and form.is_valid():
        try:
            barcode = identity.assign_barcode(product_id=form.cleaned_data['product'].pk, code=form.cleaned_data['code'], actor=request.user)
        except (ValidationError, IntegrityError) as exc:
            form.add_error(None, '; '.join(exc.messages) if isinstance(exc, ValidationError) else 'Identifier already used.')
        else:
            messages.success(request, 'Barcode assigned. Return to your invoice or stocktake and scan it again.')
            return redirect('ims:inventory:product_identity', barcode.product_id)
    return render(request, 'inventory/identity/assign.html', {'form':form})


@login_required
@ims_permission('inventory.view_productunit', staff=True)
@require_POST
@json_errors
def lookup(request):
    product, unit = identity.resolve(request.POST.get('code', ''))
    return JsonResponse({'message': f'{product.name} · {unit.get_status_display() if unit else product.sku}',
        'url': reverse('ims:inventory:unit_passport', args=[unit.unit_id]) if unit else reverse('ims:inventory:product_identity', args=[product.pk]),
        'unit_id': unit.unit_id if unit else None, 'product_id': product.pk})


@login_required
@ims_permission('inventory.view_product', staff=True)
def product_identity(request, pk):
    product = get_object_or_404(Product, pk=pk)
    form = BarcodeForm(request.POST or None, initial={'product': product, 'code': request.GET.get('code', '')})
    if request.method == 'POST':
        if not allowed(request.user, 'inventory.change_product'):
            raise PermissionDenied
        try:
            action = request.POST.get('action')
            if action == 'adopt':
                identity.adopt_existing(product_id=pk, actor=request.user, note=request.POST.get('note', ''), location=request.POST.get('location', ''))
            elif action == 'generate':
                identity.assign_barcode(product_id=pk, actor=request.user, internal=True)
            elif action == 'assign' and form.is_valid():
                identity.assign_barcode(product_id=pk, code=form.cleaned_data['code'], actor=request.user)
            else:
                raise ValidationError('Enter a valid barcode.')
        except (ValidationError, IntegrityError) as exc:
            messages.error(request, '; '.join(exc.messages) if isinstance(exc, ValidationError) else 'That identifier already exists.')
        else:
            messages.success(request, 'Inventory identity saved.')
            return redirect('ims:inventory:product_identity', pk)
    return render(request, 'inventory/identity/product.html', {'product': product, 'form': form,
        'can_manage': allowed(request.user, 'inventory.change_product'), 'can_labels': allowed(request.user, 'inventory.print_inventory_labels'),
        'units': product.units.order_by('-pk')[:100], 'physical_count': product.units.filter(status__in=identity.ON_HAND).count()})


@login_required
@ims_permission('inventory.view_productunit', staff=True)
def passport(request, unit_id):
    unit = get_object_or_404(ProductUnit.objects.select_related('product', 'shipment__supplier', 'sale_line__invoice__customer', 'order_item__order'), unit_id=unit_id)
    form = UnitActionForm(request.POST or None, unit=unit)
    if request.method == 'POST':
        if not can_manage(request.user):
            raise PermissionDenied
        if form.is_valid():
            try:
                data = form.cleaned_data
                if data['action'] == 'correct_serial':
                    identity.correct_serial(unit_id=unit.pk, serial=data['serial'], note=data['note'], actor=request.user)
                else:
                    identity.transition_unit(unit_id=unit.pk, action=data['action'], note=data['note'], location=data['location'], actor=request.user)
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, 'Unit action recorded.')
                return redirect('ims:inventory:unit_passport', unit_id)
    return render(request, 'inventory/identity/passport.html', {'unit': unit, 'form': form,
        'history': Paginator(unit.events.select_related('actor', 'invoice'), 50).get_page(request.GET.get('page')), 'sales': unit.sales_history.select_related('invoice', 'order_item__order'),
        'can_sales': allowed(request.user, 'sales.view_invoice', staff=True), 'can_manage': can_manage(request.user),
        'can_labels': allowed(request.user, 'inventory.print_inventory_labels'), 'case_form': CaseForm()})


@login_required
@ims_permission('inventory.print_inventory_labels')
@require_POST
def labels(request):
    from inventory.services.labels import render_labels
    try:
        ids = list(dict.fromkeys(int(v) for v in request.POST.getlist('ids')))
    except (TypeError, ValueError):
        return HttpResponse('Select valid labels.', status=400)
    kind = request.POST.get('kind', 'unit')
    if kind not in ('unit', 'product'):
        return HttpResponse('Choose unit or product labels.', status=400)
    all_matching = request.POST.get('all_matching') == '1'
    if all_matching and kind != 'unit':
        return HttpResponse('Select all is available for physical units.', status=400)
    if all_matching:
        objects = list(filtered_units(request.POST)[:1001])
    else:
        if not 1 <= len(ids) <= 1000:
            return HttpResponse('Select between 1 and 1,000 labels.', status=400)
        objects = list((ProductUnit if kind == 'unit' else ProductBarcode).objects.filter(pk__in=ids).select_related('product').order_by('product__name', 'pk'))
    if not objects or len(objects) > 1000:
        return HttpResponse('Choose 1–1,000 matching units. Narrow your filters for larger inventories; no labels were generated.', status=400)
    if not all_matching and len(objects) != len(ids):
        return HttpResponse('One or more selected identities were not found.', status=400)
    payload = [{'code': obj.unit_id if kind == 'unit' else obj.code, 'name': obj.product.name, 'kind': kind,
        'serial': obj.serial_number or '' if kind == 'unit' else ''} for obj in objects]
    # Long external barcodes remain usable for scanning; internal IDs keep labels readable.
    if any(len(row['code']) > 36 for row in payload):
        return HttpResponse('This external code is too long for this label size. Generate a Boforg product label instead.', status=400)
    pdf = render_labels(payload, thermal=request.POST.get('layout') == 'thermal')
    with transaction.atomic():
        for obj in objects:
            identity.event(obj if kind == 'unit' else None, product=obj.product, kind='LABEL_EXPORTED', actor=request.user,
                note='PDF generated (print/reprint); physical printing is not observable.')
    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = 'inline; filename="boforg-labels.pdf"'
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
@ims_permission('inventory.view_stocktake', staff=True)
def stocktakes(request):
    form = StocktakeForm(request.POST or None)
    if request.method == 'POST':
        if not allowed(request.user, 'inventory.add_stocktake', staff=True):
            raise PermissionDenied
        if form.is_valid():
            try:
                session = stocktake.start_stocktake(**form.cleaned_data, actor=request.user)
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                return redirect('ims:inventory:stocktake_detail', session.pk)
    return render(request, 'inventory/identity/stocktakes.html', {'form': form, 'sessions': Stocktake.objects.order_by('-pk')[:50]})


@login_required
@ims_permission('inventory.view_stocktake', staff=True)
def stocktake_detail(request, pk):
    session = get_object_or_404(Stocktake, pk=pk)
    if request.method == 'POST':
        action = request.POST.get('action')
        required = 'inventory.approve_stocktake' if action == 'approve' else 'inventory.change_stocktake'
        if not allowed(request.user, required, staff=action != 'approve'):
            raise PermissionDenied
        try:
            if action == 'submit':
                stocktake.submit_stocktake(session_id=pk)
            elif action == 'count':
                try:
                    line_id = int(request.POST.get('line_id', ''))
                except ValueError:
                    raise ValidationError('Choose a quantity product to count.')
                stocktake.set_quantity_count(session_id=pk, line_id=line_id, count=request.POST.get('count'), reason=request.POST.get('reason', ''), actor=request.user)
            elif action == 'approve':
                decisions = {k[9:]: v for k, v in request.POST.items() if k.startswith('decision-')}
                stocktake.approve_stocktake(session_id=pk, decisions=decisions, reason=request.POST.get('reason', ''), actor=request.user)
            else:
                raise ValidationError('Unknown stocktake action.')
        except ValidationError as exc:
            messages.error(request, '; '.join(exc.messages))
        else:
            return redirect('ims:inventory:stocktake_detail', pk)
    lines = session.lines.select_related('product', 'unit').order_by('product__name', 'pk')
    if request.GET.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="stocktake-{pk}.csv"'
        writer = csv.writer(response)
        writer.writerow(['Product', 'Unit', 'Expected', 'Counted', 'Variance', 'Decision'])
        for row in lines.iterator():
            writer.writerow([csv_safe(row.product.name), row.unit.unit_id if row.unit_id else '', row.expected, row.counted, row.variance, row.decision])
        return response
    return render(request, 'inventory/identity/stocktake.html', {'session': session, 'lines': lines,
        'quantity_lines': lines.filter(unit__isnull=True, product__tracking_mode=Product.TRACK_QUANTITY),
        'can_approve': allowed(request.user, 'inventory.approve_stocktake'), 'scans': session.scans.order_by('-pk')[:20]})


@login_required
@ims_permission('inventory.change_stocktake', staff=True)
@require_POST
@json_errors
def stocktake_scan(request, pk):
    get_object_or_404(Stocktake, pk=pk)
    return JsonResponse(stocktake.scan_stocktake(session_id=pk, code=request.POST.get('code', ''), key=request.POST.get('key'), actor=request.user))


@login_required
@ims_permission('inventory.manage_unit_lifecycle')
@require_POST
def case_open(request, unit_id):
    unit = get_object_or_404(ProductUnit, unit_id=unit_id)
    form = CaseForm(request.POST)
    if form.is_valid():
        try:
            case = after_sales.open_case(unit_id=unit.pk, problem=form.cleaned_data['problem'], actor=request.user)
        except ValidationError as exc:
            messages.error(request, '; '.join(exc.messages))
        else:
            return redirect('ims:inventory:case_detail', case.pk)
    else:
        messages.error(request, 'Describe the issue (up to 4,000 characters).')
    return redirect('ims:inventory:unit_passport', unit_id)


@login_required
@ims_permission('inventory.view_servicecase', staff=True)
def case_detail(request, pk):
    case = get_object_or_404(ServiceCase.objects.select_related('unit__product', 'sale'), pk=pk)
    form = CaseUpdateForm(request.POST or None, product_id=case.unit.product_id, initial={'inspection': case.inspection, 'resolution': case.resolution})
    if request.method == 'POST':
        if not can_manage(request.user):
            raise PermissionDenied
        if form.is_valid():
            try:
                after_sales.update_case(case_id=pk, actor=request.user, **form.cleaned_data)
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                return redirect('ims:inventory:case_detail', pk)
    return render(request, 'inventory/identity/case.html', {'case': case, 'form': form, 'can_manage': can_manage(request.user)})


@login_required
@ims_permission('inventory.view_productunit', staff=True)
def scanner_test(request):
    return render(request, 'inventory/identity/scanner_test.html')

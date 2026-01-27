from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from .forms import (
    ProductForm,
    ProductLandedCostForm,
    SerialProfitLookupForm,
    ShipmentCostForm,
    ShipmentForm,
    ShipmentItemForm,
    ShipmentItemReceiptForm,
    StockMovementForm,
)
from .models import (
    Category,
    Product,
    ProductUnit,
    Shipment,
    ShipmentCost,
    ShipmentItem,
    StockMovement,
    Supplier,
)
from .services import (
    ShipmentServiceError,
    landed_cost_per_product,
    profit_per_serial,
    receive_shipment,
    shipment_cost_summary,
    shipment_delay_report,
    supplier_defect_rate,
)

@login_required
def product_list(request):
    q = request.GET.get('q','')
    low = request.GET.get('low')
    qs = Product.objects.all()
    if q:
        qs = qs.filter(
            Q(name__icontains=q)|Q(sku__icontains=q)|Q(category__name__icontains=q)|Q(supplier__name__icontains=q)
        )
    if low is not None:
        qs = qs.filter(quantity__lte=5)
    qs = qs.select_related('category','supplier').order_by('name')
    paginator = Paginator(qs, 20)
    page = request.GET.get('page')
    products = paginator.get_page(page)
    return render(request,'inventory/product_list.html',{'products':products,'q':q,'low':low})

@login_required
def product_create(request):
    form = ProductForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        return redirect('ims:inventory:product_list')
    return render(request, 'inventory/product_form.html', {'form': form, 'product': form.instance})

@login_required
def product_edit(request, pk):
    product = get_object_or_404(Product, pk=pk)
    form = ProductForm(request.POST or None, request.FILES or None, instance=product)
    if request.method == 'POST' and form.is_valid():
        form.save()
        return redirect('ims:inventory:product_list')
    return render(request, 'inventory/product_form.html', {'form': form, 'product': product})

@login_required
def product_delete(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if not request.user.is_superuser:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden('Only admin can delete products')
    product.delete()
    return redirect('ims:inventory:product_list')

@login_required
def movement_create(request):
    form = StockMovementForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        m = form.save(commit=False)
        m.user = request.user
        m.save()
        return redirect('ims:inventory:product_list')
    return render(request,'inventory/movement_form.html',{'form':form})


@login_required
def shipment_list(request):
    status = request.GET.get('status')
    shipments = Shipment.objects.select_related('supplier').order_by('-created_at')
    if status:
        shipments = shipments.filter(status=status)
    return render(
        request,
        'inventory/shipment_list.html',
        {
            'shipments': shipments,
            'status_filter': status,
            'status_choices': Shipment.STATUS_CHOICES,
        },
    )


@login_required
def shipment_create(request):
    form = ShipmentForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        shipment = form.save(commit=False)
        shipment.created_by = request.user
        shipment.save()
        messages.success(request, 'Shipment created.')
        return redirect('ims:inventory:shipment_detail', shipment.id)
    return render(request, 'inventory/shipment_form.html', {'form': form})


@login_required
def shipment_detail(request, pk):
    shipment = get_object_or_404(Shipment, pk=pk)
    item_form = ShipmentItemForm(prefix='item', shipment=shipment)
    cost_form = ShipmentCostForm(prefix='cost')
    if request.method == 'POST':
        if 'add_item' in request.POST:
            item_form = ShipmentItemForm(request.POST, prefix='item', shipment=shipment)
            if item_form.is_valid():
                item = item_form.save(commit=False)
                item.shipment = shipment
                if not item.tracking_mode and item.product:
                    item.tracking_mode = item.product.tracking_mode
                item.save()
                messages.success(request, 'Shipment item added.')
                return redirect('ims:inventory:shipment_detail', shipment.id)
        elif 'add_cost' in request.POST:
            cost_form = ShipmentCostForm(request.POST, request.FILES, prefix='cost')
            if cost_form.is_valid():
                cost = cost_form.save(commit=False)
                cost.shipment = shipment
                cost.save()
                messages.success(request, 'Shipment cost added.')
                return redirect('ims:inventory:shipment_detail', shipment.id)
        elif 'update_status' in request.POST:
            new_status = request.POST.get('status')
            note = request.POST.get('note', '')
            try:
                shipment.transition_status(new_status, actor=request.user, note=note)
                messages.success(request, 'Shipment status updated.')
                return redirect('ims:inventory:shipment_detail', shipment.id)
            except ValidationError as exc:
                messages.error(request, '; '.join(exc.messages))

    items = list(
        shipment.items.select_related('product').prefetch_related('units').order_by('product__name')
    )
    costs = list(shipment.costs.all())
    purchase_total = sum((item.received_value for item in items), Decimal('0.00'))
    logistics_total = sum((cost.amount_base for cost in costs), Decimal('0.00'))
    grand_total = purchase_total + logistics_total
    cost_type_totals = {code: Decimal('0.00') for code, _ in ShipmentCost.TYPE_CHOICES}
    for cost in costs:
        if cost.cost_type in cost_type_totals:
            cost_type_totals[cost.cost_type] += cost.amount_base
    cost_totals_list = [
        {
            'code': code,
            'label': label,
            'amount': cost_type_totals.get(code, Decimal('0.00')),
        }
        for code, label in ShipmentCost.TYPE_CHOICES
    ]
    cost_breakdown = shipment_cost_summary(shipment.id)
    return render(
        request,
        'inventory/shipment_detail.html',
        {
            'shipment': shipment,
            'item_form': item_form,
            'cost_form': cost_form,
            'cost_breakdown': cost_breakdown,
            'items': items,
            'shipment_costs': costs,
            'purchase_total': purchase_total,
            'logistics_total': logistics_total,
            'grand_total': grand_total,
            'cost_totals_list': cost_totals_list,
        },
    )


@login_required
def shipment_receive(request, pk):
    shipment = get_object_or_404(Shipment, pk=pk)
    if shipment.status not in {Shipment.STATUS_ARRIVED, Shipment.STATUS_CLEARED}:
        messages.error(request, 'Shipment must be arrived or cleared before receiving.')
        return redirect('ims:inventory:shipment_detail', shipment.id)
    pending_items = [item for item in shipment.items.select_related('product') if item.quantity_received < item.quantity_expected]
    receipt_forms = [
        ShipmentItemReceiptForm(request.POST or None, prefix=f'item-{item.id}', item=item)
        for item in pending_items
    ]
    if request.method == 'POST':
        valid = all(form.is_valid() for form in receipt_forms)
        if valid:
            receipts = []
            for form in receipt_forms:
                quantity = form.cleaned_data['quantity']
                if quantity > 0:
                    receipts.append({
                        'item_id': form.cleaned_data['item_id'],
                        'quantity': quantity,
                        'serials': form.cleaned_data['serial_list'],
                    })
            if not receipts:
                messages.error(request, 'Enter at least one quantity to receive.')
            else:
                try:
                    receive_shipment(
                        shipment_id=shipment.id,
                        receipts=receipts,
                        received_by=request.user,
                    )
                    messages.success(request, 'Shipment received successfully.')
                    return redirect('ims:inventory:shipment_detail', shipment.id)
                except ShipmentServiceError as exc:
                    messages.error(request, exc.messages[0] if isinstance(exc.messages, list) else str(exc))
    return render(
        request,
        'inventory/shipment_receive.html',
        {
            'shipment': shipment,
            'forms': receipt_forms,
        },
    )


@login_required
def shipment_dashboard(request):
    def paginate_items(items, param_name):
        paginator = Paginator(items, 5)
        page_number = request.GET.get(param_name)
        return paginator.get_page(page_number)

    def pagination_query(param_name):
        querydict = request.GET.copy()
        querydict.pop(param_name, None)
        base = querydict.urlencode()
        return f"{base}&{param_name}=" if base else f"{param_name}="

    product_id = request.GET.get('product_id')
    product_initial = {'product': product_id} if product_id else None
    if request.method == 'POST' and 'lookup_product' in request.POST:
        product_form = ProductLandedCostForm(request.POST, prefix='product')
    else:
        product_form = ProductLandedCostForm(initial=product_initial, prefix='product')
    serial_form = SerialProfitLookupForm(
        request.POST if request.method == 'POST' and 'lookup_serial' in request.POST else None,
        prefix='serial',
    )
    serial_result = None
    if request.method == 'POST':
        if 'lookup_product' in request.POST and product_form.is_valid():
            product = product_form.cleaned_data['product']
            return redirect(f"{request.path}?product_id={product.id}")
        if 'lookup_serial' in request.POST and serial_form.is_valid():
            try:
                serial_result = profit_per_serial(serial_form.cleaned_data['serial_number'])
            except ProductUnit.DoesNotExist:
                serial_form.add_error('serial_number', 'Serial number not found.')
    landed_rows = None
    if product_id:
        try:
            product_obj = Product.objects.get(pk=product_id)
            landed_rows = list(landed_cost_per_product(product_obj.id))
        except (Product.DoesNotExist, ValueError):
            landed_rows = []
    shipments_qs = Shipment.objects.select_related('supplier').order_by('-created_at')
    recent_shipments_page = paginate_items(shipments_qs, 'ship_page')
    cost_summaries = [
        shipment_cost_summary(s.id)
        for s in recent_shipments_page.object_list
    ]
    suppliers_qs = Supplier.objects.order_by('name')
    supplier_stats_page = paginate_items(suppliers_qs, 'supplier_page')
    supplier_stats_rows = []
    for supplier in supplier_stats_page.object_list:
        stats = supplier_defect_rate(supplier.id)
        stats['supplier'] = supplier
        supplier_stats_rows.append(stats)
    supplier_stats_page.object_list = supplier_stats_rows
    delays_list = shipment_delay_report()
    delays_page = paginate_items(delays_list, 'delay_page')
    landed_rows_page = paginate_items(landed_rows, 'landed_page') if landed_rows is not None else None
    return render(
        request,
        'inventory/shipment_dashboard.html',
        {
            'recent_shipments_page': recent_shipments_page,
            'ship_page_query': pagination_query('ship_page'),
            'cost_summaries': cost_summaries,
            'supplier_stats_page': supplier_stats_page,
            'supplier_page_query': pagination_query('supplier_page'),
            'delays_page': delays_page,
            'delay_page_query': pagination_query('delay_page'),
            'product_form': product_form,
            'serial_form': serial_form,
            'landed_rows_page': landed_rows_page,
            'landed_page_query': pagination_query('landed_page'),
            'serial_result': serial_result,
        },
    )

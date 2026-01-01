from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import render, redirect, get_object_or_404

from .forms import (
    ProductForm,
    StockMovementForm,
    ShipmentForm,
    ShipmentItemForm,
    ShipmentCostForm,
    ShipmentItemReceiptForm,
    ProductLandedCostForm,
    SerialProfitLookupForm,
)
from .models import Product, Category, Supplier, StockMovement, Shipment, ShipmentItem, ShipmentCost, ProductUnit
from .services import (
    receive_shipment,
    ShipmentServiceError,
    shipment_cost_summary,
    landed_cost_per_product,
    supplier_defect_rate,
    shipment_delay_report,
    profit_per_serial,
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
            cost_form = ShipmentCostForm(request.POST, prefix='cost')
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

    cost_breakdown = shipment_cost_summary(shipment.id)
    return render(
        request,
        'inventory/shipment_detail.html',
        {
            'shipment': shipment,
            'item_form': item_form,
            'cost_form': cost_form,
            'cost_breakdown': cost_breakdown,
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
    product_form = ProductLandedCostForm(
        request.POST if request.method == 'POST' and 'lookup_product' in request.POST else None,
        prefix='product',
    )
    serial_form = SerialProfitLookupForm(
        request.POST if request.method == 'POST' and 'lookup_serial' in request.POST else None,
        prefix='serial',
    )
    landed_rows = None
    serial_result = None
    if request.method == 'POST':
        if 'lookup_product' in request.POST and product_form.is_valid():
            product = product_form.cleaned_data['product']
            landed_rows = list(landed_cost_per_product(product.id))
        if 'lookup_serial' in request.POST and serial_form.is_valid():
            try:
                serial_result = profit_per_serial(serial_form.cleaned_data['serial_number'])
            except ProductUnit.DoesNotExist:
                serial_form.add_error('serial_number', 'Serial number not found.')
    recent_shipments = Shipment.objects.select_related('supplier').order_by('-created_at')[:5]
    cost_summaries = [
        shipment_cost_summary(s.id)
        for s in recent_shipments
    ]
    supplier_stats = []
    for supplier in Supplier.objects.all()[:5]:
        stats = supplier_defect_rate(supplier.id)
        stats['supplier'] = supplier
        supplier_stats.append(stats)
    delays = shipment_delay_report()
    return render(
        request,
        'inventory/shipment_dashboard.html',
        {
            'recent_shipments': recent_shipments,
            'cost_summaries': cost_summaries,
            'supplier_stats': supplier_stats,
            'delays': delays,
            'product_form': product_form,
            'serial_form': serial_form,
            'landed_rows': landed_rows,
            'serial_result': serial_result,
        },
    )

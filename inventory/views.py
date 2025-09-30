from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test, permission_required
from django.db.models import Q, F
from .models import Product, Category, Supplier, StockMovement
from .forms import ProductForm, StockMovementForm
from django.core.paginator import Paginator

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

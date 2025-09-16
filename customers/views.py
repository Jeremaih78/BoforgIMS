from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.core.paginator import Paginator
from .models import Customer
from .forms import CustomerForm

@login_required
def customer_list(request):
    q = request.GET.get('q','')
    qs = Customer.objects.filter(
        Q(name__icontains=q)|Q(email__icontains=q)|Q(phone__icontains=q)
    )
    paginator = Paginator(qs.order_by('name'), 20)
    page = request.GET.get('page')
    customers = paginator.get_page(page)
    return render(request,'customers/customer_list.html',{'customers':customers,'q':q})

@login_required
def customer_create(request):
    form = CustomerForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        return redirect('customer_list')
    return render(request,'customers/customer_form.html',{'form':form})

@login_required
def customer_edit(request, pk):
    c = get_object_or_404(Customer, pk=pk)
    form = CustomerForm(request.POST or None, instance=c)
    if request.method == 'POST' and form.is_valid():
        form.save()
        return redirect('customer_list')
    return render(request,'customers/customer_form.html',{'form':form})

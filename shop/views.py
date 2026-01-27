from __future__ import annotations

import logging
import os
from typing import Optional

from django.conf import settings
from django.contrib import messages
from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.core.cache import cache
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db import OperationalError, ProgrammingError
from django.http import (Http404, HttpResponse, HttpResponseBadRequest,
                         HttpResponseRedirect, JsonResponse)
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from inventory.models import Category, Product
from payments import paynow

from .forms import CheckoutForm
from .models import Order, Payment
from .services import (OrderCreationError, create_order_from_cart,
                       mark_order_as_failed, mark_order_as_paid)
from .utils import (add_product_to_cart, cart_item_count, cart_totals,
                    clear_cart, get_or_create_cart, remove_product_from_cart)

logger = logging.getLogger(__name__)


CATALOG_PAGE_SIZE = 12


def _resolve_category(value: str) -> Optional[Category]:
    if not value:
        return None
    try:
        return Category.objects.get(slug=value)
    except Category.DoesNotExist:
        try:
            return Category.objects.get(pk=int(value))
        except (Category.DoesNotExist, ValueError, TypeError):
            return None


@require_GET
def catalog(request):
    category_param = request.GET.get('category', '').strip()
    query = request.GET.get('q', '').strip()
    page_number = request.GET.get('page', '1')

    category = _resolve_category(category_param)

    products = Product.objects.filter(is_active=True).select_related('category')
    if category:
        products = products.filter(category=category)

    if query:
        try:
            vector = (SearchVector('name', weight='A') +
                      SearchVector('description', weight='B'))
            search_query = SearchQuery(query)
            products = (
                products
                .annotate(rank=SearchRank(vector, search_query))
                .filter(rank__gte=0.1)
                .order_by('-rank', '-updated_at')
            )
        except (ProgrammingError, OperationalError):
            products = products.filter(name__icontains=query)
    else:
        products = products.order_by('-updated_at')

    paginator = Paginator(products, CATALOG_PAGE_SIZE)
    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    categories_cache_key = 'shop:categories:v1'
    categories = cache.get(categories_cache_key)
    if categories is None:
        categories = list(
            Category.objects.filter(product__is_active=True)
            .distinct()
            .order_by('name')
        )
        cache.set(categories_cache_key, categories, getattr(settings, 'CACHE_TTL_CATALOG', 120))

    cart = get_or_create_cart(request)

    context = {
        'page_obj': page_obj,
        'products': page_obj.object_list,
        'paginator': paginator,
        'selected_category': category,
        'query': query,
        'categories': categories,
        'cart_count': cart_item_count(cart),
        'cart_total': cart_totals(cart),
    }
    return render(request, 'shop/catalog.html', context)


@require_GET
def product_detail(request, slug):
    product = get_object_or_404(
        Product.objects.select_related('category', 'supplier'),
        slug=slug,
        is_active=True,
    )
    related_products = (
        Product.objects.filter(is_active=True, category=product.category)
        .exclude(pk=product.pk)
        .order_by('-updated_at')[:4]
    )

    cart = get_or_create_cart(request)

    return render(
        request,
        'shop/product_detail.html',
        {
            'product': product,
            'related_products': related_products,
            'cart_count': cart_item_count(cart),
        },
    )


@require_GET
def cart_detail(request):
    cart = get_or_create_cart(request)
    items = cart.items.select_related('product', 'product__category')
    return render(
        request,
        'shop/cart_detail.html',
        {
            'cart': cart,
            'items': items,
            'cart_count': cart_item_count(cart),
            'subtotal': cart_totals(cart),
        },
    )


@require_POST
def cart_add(request):
    product_id = request.POST.get('product_id')
    quantity = request.POST.get('quantity', '1')
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        quantity = 1
    quantity = max(quantity, 1)

    product = get_object_or_404(Product, pk=product_id, is_active=True)
    cart = get_or_create_cart(request)

    existing_qty = cart.items.filter(product=product).values_list('quantity', flat=True).first() or 0
    if product.track_inventory and (existing_qty + quantity) > product.available_stock:
        if request.headers.get('HX-Request') == 'true':
            html = render_to_string("shop/partials/cart_counter.html", {"count": cart_item_count(cart)}, request=request)
            response = HttpResponse(html)
            response.status_code = 409
            return response
        messages.error(request, f"Not enough stock for {product.name}.")
        return redirect(product.get_absolute_url())

    add_product_to_cart(cart, product, quantity)

    if request.headers.get('HX-Request') == 'true':
        html = render_to_string(
            'shop/partials/cart_counter.html',
            {'count': cart_item_count(cart)},
            request=request,
        )
        return HttpResponse(html)

    messages.success(request, f"Added {product.name} to cart.")
    next_url = request.POST.get('next') or product.get_absolute_url()
    return redirect(next_url)


@require_POST
def cart_remove(request):
    product_id = request.POST.get('product_id')
    product = get_object_or_404(Product, pk=product_id)
    cart = get_or_create_cart(request)
    remove_product_from_cart(cart, product)

    if request.headers.get('HX-Request') == 'true':
        html = render_to_string(
            'shop/partials/cart_counter.html',
            {'count': cart_item_count(cart)},
            request=request,
        )
        return HttpResponse(html)

    messages.info(request, f"Removed {product.name} from cart.")
    return redirect('shop:cart_detail')


def checkout(request):
    cart = get_or_create_cart(request)
    items = list(cart.items.select_related('product'))
    if not items:
        messages.info(request, 'Your cart is empty.')
        return redirect('shop:catalog')

    if request.method == 'POST':
        form = CheckoutForm(request.POST)
        if form.is_valid():
            try:
                result = create_order_from_cart(
                    cart,
                    email=form.cleaned_data['email'],
                    full_name=form.cleaned_data.get('full_name', ''),
                    notes=form.cleaned_data.get('notes', ''),
                )
            except OrderCreationError as exc:
                logger.warning('Order creation failed: %s', exc)
                messages.error(request, str(exc))
                return redirect('shop:cart_detail')

            order = result.order
            payment, _ = Payment.objects.get_or_create(order=order)
            payment.status = Payment.Status.INITIATED
            payment.save(update_fields=['status', 'updated_at'])

            request.session['shop_last_order'] = order.number
            return redirect(f"{reverse('shop:paynow_initiate')}?order={order.number}")
    else:
        form = CheckoutForm()

    subtotal = sum(item.line_total for item in items)

    return render(
        request,
        'shop/checkout.html',
        {
            'cart': cart,
            'items': items,
            'subtotal': subtotal,
            'form': form,
        },
    )


def checkout_complete(request):
    order_number = request.session.get('shop_last_paid_order')
    if not order_number:
        return redirect('shop:catalog')
    order = get_object_or_404(Order, number=order_number)
    return render(request, 'shop/checkout_complete.html', {'order': order})


def paynow_initiate(request):
    order_number = request.GET.get('order') or request.session.get('shop_last_order')
    if not order_number:
        messages.error(request, 'Order not found for payment.')
        return redirect('shop:checkout')

    order = get_object_or_404(Order, number=order_number)
    payment, _ = Payment.objects.get_or_create(order=order)

    if order.status == Order.Status.PAID:
        messages.info(request, 'This order is already paid.')
        request.session['shop_last_paid_order'] = order.number
        return redirect('shop:checkout_complete')

    public_base = os.environ.get('SHOP_PUBLIC_BASE', getattr(settings, 'PUBLIC_BASE_URL', ''))
    if not public_base:
        public_base = request.build_absolute_uri('/').rstrip('/')
    return_url = f"{public_base}{reverse('shop:paynow_return')}"
    result_url = f"{public_base}{reverse('shop:paynow_result')}"

    response = paynow.create_payment(
        order_number=order.number,
        email=order.email,
        amount=order.total,
        return_url=return_url,
        result_url=result_url,
        items=[(item.product_name, item.line_total) for item in order.items.all()],
    )

    payment.raw_response = response.get('raw', {})

    if not response.get('ok'):
        payment.status = Payment.Status.FAILED
        payment.save(update_fields=['status', 'raw_response', 'updated_at'])
        mark_order_as_failed(order)
        messages.error(request, 'Could not initiate payment. Please try again or contact support.')
        return redirect('shop:checkout')

    payment.provider_ref = response.get('reference', '')
    payment.poll_url = response.get('poll_url', '')
    payment.status = Payment.Status.INITIATED
    payment.save(update_fields=['provider_ref', 'poll_url', 'status', 'raw_response', 'updated_at'])

    redirect_url = response.get('redirect_url')
    if redirect_url:
        return HttpResponseRedirect(redirect_url)

    messages.error(request, 'Payment provider did not return a redirect URL.')
    return redirect('shop:checkout')


def _handle_payment_status(order: Order, payment: Payment, status: str, payload: dict) -> None:
    status_lower = status.lower() if status else ''
    if status_lower == 'paid':
        if payment.status != Payment.Status.PAID:
            payment.status = Payment.Status.PAID
            payment.raw_response = payload
            payment.save(update_fields=['status', 'raw_response', 'updated_at'])
            mark_order_as_paid(order)
            logger.info('Order %s marked as paid.', order.number)
        else:
            payment.raw_response = payload or payment.raw_response
            payment.save(update_fields=['raw_response', 'updated_at'])
    elif status_lower in {'failed', 'cancelled'}:
        if payment.status != Payment.Status.FAILED:
            payment.status = Payment.Status.FAILED
            payment.raw_response = payload
            payment.save(update_fields=['status', 'raw_response', 'updated_at'])
            mark_order_as_failed(order)
            logger.info('Order %s marked as failed.', order.number)
        else:
            payment.raw_response = payload or payment.raw_response
            payment.save(update_fields=['raw_response', 'updated_at'])
    else:
        logger.info('Order %s payment status %s ignored.', order.number, status)


def paynow_return(request):
    reference = request.GET.get('reference') or request.GET.get('order') or request.session.get('shop_last_order')
    if not reference:
        messages.error(request, 'Missing payment reference.')
        return redirect('shop:checkout')

    order = get_object_or_404(Order, number=reference)
    payment = getattr(order, 'payment', None)
    if not payment:
        messages.error(request, 'No payment record found for this order.')
        return redirect('shop:checkout')

    status_payload = {}
    if payment.poll_url:
        status_payload = paynow.poll_status(payment.poll_url)
    status = status_payload.get('status') or request.GET.get('status', '')
    if not status:
        messages.info(request, 'Payment status could not be verified yet. Please wait a moment and refresh.')
        return redirect('shop:checkout')

    _handle_payment_status(order, payment, status, status_payload.get('raw', {}))

    if payment.is_paid:
        cart = get_or_create_cart(request)
        clear_cart(cart)
        request.session['shop_last_paid_order'] = order.number
        request.session.pop('shop_last_order', None)
        messages.success(request, 'Payment received! Your order is confirmed.')
        return redirect('shop:checkout_complete')

    messages.error(request, 'Payment was not successful. Please try again.')
    return redirect('shop:checkout')


@csrf_exempt
@require_POST
def paynow_result(request):
    reference = request.POST.get('reference') or request.POST.get('order')
    status = request.POST.get('status')
    if not reference or not status:
        return HttpResponseBadRequest('Missing reference or status')

    order = get_object_or_404(Order, number=reference)
    payment = getattr(order, 'payment', None)
    if not payment:
        return HttpResponseBadRequest('Payment record not found')

    raw_payload = request.POST.dict()
    _handle_payment_status(order, payment, status, raw_payload)

    return HttpResponse('OK')


@require_GET
def healthcheck(request):
    return JsonResponse({'ok': True})

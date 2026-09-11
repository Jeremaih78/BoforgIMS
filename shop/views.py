from __future__ import annotations
from django.db import transaction
from django.db.models import Q, F
from django.utils.http import url_has_allowed_host_and_scheme
from .selectors import public_products
from .context_processors import whatsapp_url
import json
from django.utils.safestring import mark_safe
from decimal import Decimal, InvalidOperation

import logging
import os
from typing import Optional
from urllib.parse import urlencode

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



def _build_whatsapp_order_url(items, subtotal, packages=()) -> str:
    product_blocks = []
    for position, item in enumerate(items, start=1):
        product_blocks.append(
            '\n'.join(
                [
                    f'{position}. {item.product.name}',
                    f'SKU: {item.product.sku}',
                    f'Unit Price: ${item.product.price:.2f}',
                    f'Quantity: {item.quantity}',
                    f'Total: ${item.line_total:.2f}',
                ]
            )
        )

    for package in packages:
        product_blocks.append(f'{package.quantity} x {package.combo.name} ({package.combo.code}): {package.line_total:.2f}')
    message = '\n\n'.join(
        [
            'Hello Boforg Technologies,',
            'I would like to place the following order:',
            *product_blocks,
            f'Order Subtotal: ${subtotal:.2f}',
            'Please confirm availability, payment instructions, and collection or delivery arrangements.',
        ]
    )
    return whatsapp_url(message)


def _cart_detail_context(cart) -> dict:
    items = list(cart.items.select_related('product', 'product__category'))
    subtotal = cart_totals(cart)
    packages = list(cart.packages.select_related('combo').prefetch_related('combo__items__product'))
    return {
        'cart': cart,
        'page_event': {'event': 'view_cart'},
        'items': items,
        'cart_packages': packages,
        'cart_count': cart_item_count(cart),
        'subtotal': subtotal,
        'whatsapp_order_url': _build_whatsapp_order_url(items, subtotal, packages) if items or packages else '',
    }


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
    category = _resolve_category(request.GET.get('category', '')[:120])
    query = request.GET.get('q', '').strip()[:100]
    department = request.GET.get('department', '')
    products = public_products()
    if category:
        products = products.filter(category=category)
    if department in ('printing', 'fitness'):
        products = products.filter(category__department=department)
    for term in query.split()[:6]:
        products = products.filter(Q(name__icontains=term) | Q(sku__icontains=term) | Q(category__name__icontains=term) | Q(description__icontains=term))
    if request.GET.get('stock') == '1':
        products = products.filter(Q(track_inventory=False) | Q(quantity__gt=F('reserved')))
    for key, lookup in [('min_price', 'price__gte'), ('max_price', 'price__lte')]:
        try:
            value = Decimal(request.GET.get(key, ''))
            if value.is_finite() and 0 <= value <= Decimal('9999999999.99'):
                products = products.filter(**{lookup: value})
        except (InvalidOperation, ValueError):
            pass
    sort = request.GET.get('sort', 'newest')
    ordering = {'price_asc': 'price', 'price_desc': '-price', 'name': 'name', 'newest': '-created_at'}
    products = products.order_by(ordering.get(sort, '-created_at'), 'pk')
    page = Paginator(products, CATALOG_PAGE_SIZE).get_page(request.GET.get('page'))
    params = request.GET.copy(); params.pop('page', None)
    guided = not request.GET
    categories = Category.objects.filter(product__in=public_products()).distinct()
    context = {'page_obj': page, 'products': page.object_list, 'paginator': page.paginator,
               'selected_category': category, 'query': query, 'department': department,
               'categories': categories, 'sort': sort, 'filter_query': params.urlencode(),
               'page_event': {'event': 'search' if query else 'view_item_list'}, 'canonical_url': request.build_absolute_uri(), 'guided': guided, 'page_description': 'Shop printing equipment, supplies and home fitness in Harare, Zimbabwe.'}
    if guided:
        from .packages import public_packages
        context['packages'] = public_packages()
        context['discovery_sections'] = [(title, public_products().filter(category__name__icontains=term).order_by('-created_at')[:4]) for title, term in [('Machines', 'machine'), ('Consumables & restocking', 'consum'), ('Blanks', 'blank')]]
        context['departments'] = [('printing', 'Printing & Business Equipment'), ('fitness', 'Boforg Home Fitness')]
    return render(request, 'shop/catalog.html', context)


@require_GET
def product_detail(request, slug):
    product = get_object_or_404(
        public_products(),
        slug=slug,
        is_active=True,
    )
    related_products = (
        public_products().filter(category=product.category)
        .exclude(pk=product.pk)
        .order_by('-updated_at')[:4]
    )

    recommended = product.recommended_products.filter(is_active=True, is_public=True, price__gt=0).select_related('category')[:4]
    schema = {'@context': 'https://schema.org', '@type': 'Product', 'name': product.name, 'sku': product.sku, 'description': product.short_description or product.description or product.name, 'image': request.build_absolute_uri(product.get_primary_image_url()), 'offers': {'@type': 'Offer', 'price': str(product.price), 'priceCurrency': product.currency, 'availability': 'https://schema.org/' + ('InStock' if product.shop_purchasable else 'OutOfStock')}}
    schema['url'] = request.build_absolute_uri()
    breadcrumb = {'@context': 'https://schema.org', '@type': 'BreadcrumbList', 'itemListElement': [{'@type': 'ListItem', 'position': 1, 'name': 'Shop', 'item': request.build_absolute_uri(reverse('shop:catalog'))}, {'@type': 'ListItem', 'position': 2, 'name': product.name, 'item': request.build_absolute_uri()}]}
    breadcrumb_data = mark_safe(json.dumps(breadcrumb).replace('<', chr(92) + 'u003c'))
    structured_data = mark_safe(json.dumps(schema).replace('<', chr(92) + 'u003c'))

    return render(
        request,
        'shop/product_detail.html',
        {
            'product': product,
            'page_event': {'event': 'view_item', 'currency': product.currency, 'value': str(product.price), 'items': [{'item_id': product.sku, 'item_name': product.name, 'price': str(product.price)}]},
            'related_products': related_products,
            'recommended_products': recommended,
            'structured_data': structured_data,
            'breadcrumb_data': breadcrumb_data,
            'product_whatsapp': whatsapp_url(f'Hello Boforg Technologies, I am interested in {product.name}. Price: {product.currency} {product.price}. Product page: {request.build_absolute_uri(reverse("shop:product_detail", args=[product.slug]))}. Please assist me.'),
            'page_description': product.short_description or (product.description or product.name)[:160],
        },
    )


@require_GET
def cart_detail(request):
    cart = get_or_create_cart(request)
    return render(
        request,
        'shop/cart_detail.html',
        _cart_detail_context(cart),
    )


@require_POST
def cart_add(request):
    try:
        product_id = int(request.POST.get('product_id', ''))
    except (ValueError, TypeError):
        return HttpResponseBadRequest('Invalid product.')
    quantity = request.POST.get('quantity', '1')
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        return HttpResponseBadRequest('Quantity must be a whole number.')
    if not 1 <= quantity <= 9999:
        return HttpResponseBadRequest('Quantity must be between 1 and 9999.')

    product = get_object_or_404(public_products(), pk=product_id)
    cart = get_or_create_cart(request)

    existing_qty = cart.items.filter(product=product).values_list('quantity', flat=True).first() or 0
    if product.track_inventory and (existing_qty + quantity) > product.available_stock:
        if request.headers.get('Accept') == 'application/json':
            return JsonResponse({'message': f'Not enough stock for {product.name}.'}, status=409)
        if request.headers.get('HX-Request') == 'true':
            html = render_to_string("shop/partials/cart_counter.html", {"count": cart_item_count(cart)}, request=request)
            response = HttpResponse(html)
            response.status_code = 409
            return response
        messages.error(request, f"Not enough stock for {product.name}.")
        return redirect('shop:product_detail', slug=product.slug)

    try:
        add_product_to_cart(cart, product, quantity)
    except ValueError as exc:
        if request.headers.get('Accept') == 'application/json':
            return JsonResponse({'message': str(exc)}, status=409)
        messages.error(request, str(exc))
        return redirect('shop:product_detail', slug=product.slug)

    if request.headers.get('HX-Request') == 'true':
        html = render_to_string(
            'shop/partials/cart_counter.html',
            {'count': cart_item_count(cart)},
            request=request,
        )
        return HttpResponse(html)

    event = {'event': 'add_to_cart', 'currency': product.currency, 'value': str(product.price * quantity), 'items': [{'item_id': product.sku, 'item_name': product.name, 'price': str(product.price), 'quantity': quantity}]}
    if request.headers.get('Accept') == 'application/json':
        return JsonResponse({'message': f'{product.name} added to cart.', 'cart_count': cart_item_count(cart), 'analytics': event})
    request.session['store_events'] = [event]
    messages.success(request, f"Added {product.name} to cart.")
    next_url = request.POST.get('next', '')
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        next_url = reverse('shop:product_detail', args=[product.slug])
    return redirect(next_url)


@require_POST
def cart_remove(request):
    try:
        product_id = int(request.POST.get('product_id', ''))
    except (ValueError, TypeError):
        return HttpResponseBadRequest('Invalid product.')
    product = get_object_or_404(Product, pk=product_id)
    cart = get_or_create_cart(request)
    event = {'event': 'remove_from_cart', 'items': [{'item_id': product.sku}]}
    remove_product_from_cart(cart, product)
    if request.headers.get('Accept') == 'application/json':
        context = _cart_detail_context(cart)
        return JsonResponse({'message': f'{product.name} removed from cart.', 'cart_count': context['cart_count'],
                             'cart_html': render_to_string('shop/partials/cart_content.html', context, request=request),
                             'analytics': event})
    request.session['store_events'] = [event]

    if request.headers.get('HX-Request') == 'true':
        html = render_to_string(
            'shop/partials/cart_remove_response.html',
            _cart_detail_context(cart),
            request=request,
        )
        return HttpResponse(html)

    messages.info(request, f"Removed {product.name} from cart.")
    return redirect('shop:cart_detail')


def checkout(request):
    cart = get_or_create_cart(request)
    items = list(cart.items.select_related('product'))
    if not items and not cart.packages.exists():
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

    subtotal = cart_totals(cart)

    return render(
        request,
        'shop/checkout.html',
        {
            'cart': cart,
            'items': items,
            'subtotal': subtotal,
            'cart_packages': list(cart.packages.select_related('combo').prefetch_related('combo__items__product')),
            'form': form,
            'page_event': {'event': 'begin_checkout'},
        },
    )


def checkout_complete(request):
    order_number = request.session.get('shop_last_paid_order')
    if not order_number:
        return redirect('shop:catalog')
    order = get_object_or_404(Order, number=order_number)
    events = []
    key = 'tracked_purchase_' + order.number
    if order.is_paid and not request.session.get(key):
        events = [{'event': 'purchase', 'transaction_id': order.number, 'currency': order.currency, 'value': str(order.total)}]
        request.session[key] = True
    return render(request, 'shop/checkout_complete.html', {'order': order, 'purchase_events': events})


def paynow_initiate(request):
    order_number = request.session.get('shop_last_order')
    if not order_number:
        messages.error(request, 'Order not found for payment.')
        return redirect('shop:checkout')

    order = get_object_or_404(Order, number=order_number)
    payment, _ = Payment.objects.get_or_create(order=order)

    if order.status == Order.Status.PAID:
        messages.info(request, 'This order is already paid.')
        request.session['shop_last_paid_order'] = order.number
        return redirect('shop:checkout_complete')

    public_base = os.environ.get('SHOP_PUBLIC_BASE', '')
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


@transaction.atomic
def _handle_payment_status(order: Order, payment: Payment, status: str, payload: dict) -> None:
    order = Order.objects.select_for_update().get(pk=order.pk)
    original_payment = payment
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    if order.status in (Order.Status.PAID, Order.Status.SHIPPED):
        original_payment.status = Payment.Status.PAID
        return
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

    original_payment.status = payment.status


def _verified_status_matches(order, result):
    raw = result.get('raw') or {}
    try:
        return raw.get('reference') == order.number and Decimal(str(raw.get('amount'))) == order.total
    except (ArithmeticError, TypeError, ValueError):
        return False


def paynow_return(request):
    reference = request.session.get('shop_last_order')
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
    status = status_payload.get('status', '')
    if not _verified_status_matches(order, status_payload):
        status = ''
    if not status:
        messages.info(request, 'Payment status could not be verified yet. Please wait a moment and refresh.')
        return redirect('shop:checkout')

    try:
        _handle_payment_status(order, payment, status, status_payload.get('raw', {}))
    except OrderCreationError:
        messages.error(request, 'Payment verified; please contact staff to reconcile stock before collection.')
        return redirect('shop:checkout')

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

    verified = paynow.poll_status(payment.poll_url)
    if not _verified_status_matches(order, verified):
        return HttpResponse('Payment could not be verified; retry later.', status=503)
    try:
        _handle_payment_status(order, payment, verified.get('status', ''), verified.get('raw', {}))
    except OrderCreationError:
        logger.error('Verified order %s needs stock reconciliation.', order.pk)
        return HttpResponse('Stock reconciliation required.', status=409)

    return HttpResponse('OK')


@require_GET
def healthcheck(request):
    return JsonResponse({'ok': True})


@require_POST
@transaction.atomic
def package_add(request):
    from .packages import public_packages
    from .models import Cart, CartPackage
    try:
        quantity = int(request.POST.get('quantity', '1'))
        combo_id = int(request.POST.get('combo_id', ''))
    except ValueError:
        return HttpResponseBadRequest('Invalid package quantity.')
    cart = get_or_create_cart(request)
    Cart.objects.select_for_update().get(pk=cart.pk)
    combo = next((c for c in public_packages([combo_id]) if c.pk == combo_id), None)
    if not combo or not 1 <= quantity <= 9999:
        return HttpResponseBadRequest('Package unavailable.')
    entry, created = CartPackage.objects.get_or_create(cart=cart, combo=combo, defaults={'quantity': 0})
    if entry.quantity + quantity > combo.shop_available:
        if created:
            entry.delete()
        if request.headers.get('Accept') == 'application/json':
            return JsonResponse({'message': 'Not enough stock for this package.'}, status=409)
        messages.error(request, 'Not enough stock for this package.')
    else:
        entry.quantity += quantity
        entry.save(update_fields=['quantity'])
        if request.headers.get('Accept') == 'application/json':
            return JsonResponse({'message': f'{combo.name} added to cart.', 'cart_count': cart_item_count(cart)})
        messages.success(request, 'Package added to cart. Component availability is checked again at checkout.')
    return redirect('shop:cart_detail')


@require_POST
@transaction.atomic
def package_remove(request):
    from .models import Cart
    try:
        package_id = int(request.POST.get('package_id', ''))
    except (TypeError, ValueError):
        return HttpResponseBadRequest('Invalid package.')
    cart = get_or_create_cart(request)
    Cart.objects.select_for_update().get(pk=cart.pk)
    package = cart.packages.filter(pk=package_id).select_related('combo').first()
    name = package.combo.name if package else 'Package'
    cart.packages.filter(pk=package_id).delete()
    if request.headers.get('Accept') == 'application/json':
        context = _cart_detail_context(cart)
        return JsonResponse({'message': f'{name} removed from cart.', 'cart_count': context['cart_count'],
                             'cart_html': render_to_string('shop/partials/cart_content.html', context, request=request)})
    messages.info(request, f'{name} removed from cart.')
    return redirect('shop:cart_detail')


@require_GET
def robots(request):
    sitemap_url = request.build_absolute_uri(reverse('shop:sitemap'))
    return HttpResponse(f'User-agent: *\nDisallow: /cart/\nDisallow: /checkout/\nDisallow: /paynow/\nSitemap: {sitemap_url}\n', content_type='text/plain')

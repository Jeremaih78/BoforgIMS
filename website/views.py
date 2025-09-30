from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.db.models import Count, Q, Sum
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from inventory.models import Category, Product, Supplier
from sales.models import DocumentLine


class HomeView(TemplateView):
    template_name = 'website/home.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        cache_key = 'website:home:v1'
        cached = cache.get(cache_key)
        if cached:
            ctx.update(cached)
            return ctx

        now = timezone.now()
        payload = {
            'top_sellers': self._get_top_sellers(now),
            'new_arrivals': list(self._get_new_arrivals()),
            'featured_categories': list(self._get_featured_categories()),
            'partner_brands': list(self._get_partner_brands()),
            'testimonials': self._get_testimonials(),
            'services': self._get_services(),
            'company': self._get_company_profile(),
            'now': now,
        }
        ctx.update(payload)

        cache.set(cache_key, payload, getattr(settings, 'CACHE_TTL_HOME', 300))
        return ctx

    def _get_top_sellers(self, now):
        window_start = now.date() - timedelta(days=60)
        top_lines = (
            DocumentLine.objects.filter(
                invoice__date__gte=window_start,
                product__isnull=False,
                product__is_active=True,
            )
            .values('product_id')
            .annotate(total_units=Sum('quantity'))
            .order_by('-total_units')[:12]
        )
        top_ids = [row['product_id'] for row in top_lines]
        products = Product.objects.filter(id__in=top_ids).select_related('category')
        product_map = {p.id: p for p in products}
        ordered = [product_map[pid] for pid in top_ids if pid in product_map]
        if len(ordered) < 12:
            fallback_qs = Product.objects.filter(is_active=True).exclude(id__in=top_ids)
            if 'updated_at' in [f.name for f in Product._meta.get_fields() if hasattr(f, 'name')]:
                fallback_qs = fallback_qs.order_by('-updated_at')
            else:
                fallback_qs = fallback_qs.order_by('-id')
            ordered.extend(list(fallback_qs[: 12 - len(ordered)]))
        return ordered

    def _get_new_arrivals(self):
        order_field = '-created_at' if 'created_at' in [f.name for f in Product._meta.get_fields() if hasattr(f, 'name')] else '-id'
        return (
            Product.objects.filter(is_active=True)
            .order_by(order_field)
            .select_related('category')[:12]
        )

    def _get_featured_categories(self):
        return (
            Category.objects.annotate(
                product_count=Count('product', filter=Q(product__is_active=True))
            )
            .filter(product_count__gt=0)
            .order_by('-product_count', 'name')[:8]
        )

    def _get_partner_brands(self):
        supplier_names = (
            Supplier.objects.filter(product__is_active=True)
            .order_by('name')
            .distinct()
            .values_list('name', flat=True)[:12]
        )
        return supplier_names

    def _get_testimonials(self):
        return [
            {
                'name': 'Nyasha M.',
                'role': 'Founder, Harare Print Hub',
                'quote': 'Boforg keeps our heat press line running smoothly and the support team is exceptional.'
            },
            {
                'name': 'Chenai K.',
                'role': 'SME Owner',
                'quote': 'Their AI-driven product recommendations helped us double our online conversion rate in weeks.'
            },
            {
                'name': 'Tendai P.',
                'role': 'Procurement Lead',
                'quote': 'Reliable delivery, transparent pricing, and a powerful inventory system all in one partner.'
            },
        ]

    def _get_services(self):
        return [
            {
                'title': 'Printing Hardware',
                'description': 'Industrial heat presses, sublimation printers, vinyl cutters, and accessories tailored for Zimbabwean creators.'
            },
            {
                'title': 'AI & Software Solutions',
                'description': 'Inventory intelligence, custom AI integrations, and business process automations that scale.'
            },
            {
                'title': 'E-Commerce Enablement',
                'description': 'End-to-end setup of online storefronts, payment gateways, and fulfilment workflows.'
            },
        ]

    def _get_company_profile(self):
        return {
            'name': 'Boforg Technologies Private Limited',
            'phone': '+263786264994',
            'email': 'adriannzvimbo@gmail.com',
            'address': 'Robert Mugabe Street, Harare, Zimbabwe',
            'cta_whatsapp': 'https://wa.me/263786264994',
        }


class NewsletterSubscribeView(View):
    def post(self, request, *args, **kwargs):
        email = request.POST.get('email', '').strip()
        if email:
            cache.set(
                f'website:newsletter:{email.lower()}',
                {'email': email, 'ts': timezone.now().isoformat()},
                60 * 60 * 24,
            )
            messages.success(request, 'Thanks for subscribing! We will be in touch soon.')
        else:
            messages.error(request, 'Please provide a valid email.')
        redirect_url = reverse('website:home') + '#newsletter'
        return HttpResponseRedirect(redirect_url)




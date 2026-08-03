from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from django.test import SimpleTestCase
from django.template.loader import render_to_string

from .views import _build_whatsapp_order_url


class WhatsAppOrderUrlTests(SimpleTestCase):
    def test_order_url_contains_dynamic_cart_details_and_encoded_characters(self):
        product = SimpleNamespace(
            name='Laptop & Carry Case / 15"',
            sku='BFG-LAP & CASE',
            price=Decimal('690.00'),
        )
        item = SimpleNamespace(
            product=product,
            quantity=2,
            line_total=Decimal('1380.00'),
        )

        url = _build_whatsapp_order_url([item], Decimal('1380.00'))
        parsed_url = urlparse(url)
        message = parse_qs(parsed_url.query)['text'][0]

        self.assertEqual(parsed_url.netloc, 'wa.me')
        self.assertEqual(parsed_url.path, '/263786264994')
        self.assertIn('1. Laptop & Carry Case / 15"', message)
        self.assertIn('SKU: BFG-LAP & CASE', message)
        self.assertIn('Unit Price: $690.00', message)
        self.assertIn('Quantity: 2', message)
        self.assertIn('Total: $1380.00', message)
        self.assertIn('Order Subtotal: $1380.00', message)
        self.assertIn('%26', parsed_url.query)


class CartRemovalTemplateTests(SimpleTestCase):
    def test_remove_form_replaces_cart_content(self):
        product = SimpleNamespace(
            id=42,
            name='Business Laptop',
            sku='BFG-LAP-I5',
            price=Decimal('690.00'),
            get_primary_image_url='/static/shop/product.jpg',
        )
        item = SimpleNamespace(
            product=product,
            quantity=1,
            line_total=Decimal('690.00'),
        )

        html = render_to_string(
            'shop/partials/cart_content.html',
            {
                'items': [item],
                'subtotal': Decimal('690.00'),
                'whatsapp_order_url': 'https://wa.me/example',
            },
        )

        self.assertIn('id="cart-content"', html)
        self.assertIn('hx-target="#cart-content"', html)
        self.assertIn('hx-swap="outerHTML"', html)

    def test_remove_response_updates_counter_and_empty_cart(self):
        html = render_to_string(
            'shop/partials/cart_remove_response.html',
            {
                'items': [],
                'subtotal': Decimal('0.00'),
                'cart_count': 0,
                'whatsapp_order_url': '',
            },
        )

        self.assertIn('Your cart is empty.', html)
        self.assertIn('id="cart-counter"', html)
        self.assertIn('hx-swap-oob="outerHTML"', html)
        self.assertNotIn('Place Order via WhatsApp', html)

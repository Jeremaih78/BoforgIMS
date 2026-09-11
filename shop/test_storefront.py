from decimal import Decimal
import json
import re
from urllib.parse import parse_qs, urlparse
from django.test import TestCase
from django.urls import reverse
from inventory.models import Category, Product, Combo, ComboItem
from .models import Cart, CartPackage
from .services import create_order_from_cart, mark_order_as_paid, OrderCreationError
from .utils import add_product_to_cart


class StorefrontTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Heat Press Machines', department='printing')
        self.product = Product.objects.create(name='Studio press', sku='HP-123', category=self.category, price=100, quantity=5)
        self.hidden = Product.objects.create(name='Internal Transport', sku='INT-1', price=10, quantity=5, is_public=False)
        self.combo = Combo.objects.create(name='Starter Setup', code='starter', is_public=True, discount_type='percent', discount_value=10)
        ComboItem.objects.create(combo=self.combo, product=self.product, quantity=2)

    def test_home_and_guided_shop(self):
        for url in ['/', '/shop/']:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'Starter Setup')
            self.assertContains(response, '180.00')
            self.assertNotContains(response, 'From $699')

    def test_partial_category_and_sku_search(self):
        for term in ['heat', 'HP-12', 'Studio']:
            response = self.client.get('/shop/', {'q': term})
            self.assertContains(response, 'Studio press')
        self.assertNotContains(self.client.get('/shop/', {'q': 'zzzz'}), 'Studio press')

    def test_hidden_product_cannot_be_read_added_or_checked_out(self):
        self.assertNotContains(self.client.get('/shop/?all=1'), 'Internal Transport')
        self.assertEqual(self.client.get(reverse('shop:product_detail', args=[self.hidden.slug])).status_code, 404)
        self.assertEqual(self.client.post('/shop/cart/add/', {'product_id': self.hidden.pk}).status_code, 404)
        cart = Cart.objects.create(session_key='hidden-test')
        cart.items.create(product=self.hidden, quantity=1)
        with self.assertRaises(OrderCreationError):
            create_order_from_cart(cart, email='test@example.com')

    def test_stock_ui_and_backend_agree(self):
        self.product.reserved = 5; self.product.save()
        response = self.client.get(reverse('shop:product_detail', args=[self.product.slug]))
        self.assertNotContains(response, '>Add to Cart<')
        self.assertContains(response, 'Out of stock')
        self.client.post('/shop/cart/add/', {'product_id': self.product.pk})
        self.assertEqual(sum(c.item_count for c in Cart.objects.all()), 0)

    def test_non_inventory_product_can_be_bought(self):
        self.product.quantity = 0; self.product.track_inventory = False; self.product.save()
        self.assertContains(self.client.get(reverse('shop:product_detail', args=[self.product.slug])), '>Add to Cart<')

    def test_no_external_cart_redirect(self):
        response = self.client.post('/shop/cart/add/', {'product_id': self.product.pk, 'next': 'https://evil.example/'})
        self.assertEqual(response['Location'], reverse('shop:product_detail', args=[self.product.slug]))

    def test_invalid_quantities_rejected(self):
        for quantity in ['0', '-1', '1.2', 'NaN', '10000']:
            self.assertEqual(self.client.post('/shop/cart/add/', {'product_id': self.product.pk, 'quantity': quantity}).status_code, 400)

    def test_departments_price_and_stock_filters(self):
        fitness = Category.objects.create(name='Walking Pads', department='fitness')
        Product.objects.create(name='Walk at home', sku='FIT-1', category=fitness, price=200, quantity=3)
        response = self.client.get('/shop/', {'department': 'fitness', 'min_price': '150', 'stock': '1'})
        self.assertContains(response, 'Walk at home')
        self.assertNotContains(response, 'Studio press')
        self.assertEqual(self.client.get('/shop/?min_price=NaN').status_code, 200)

    def test_recommendations_exclude_private_records(self):
        self.product.recommended_products.add(self.hidden)
        self.assertNotContains(self.client.get(reverse('shop:product_detail', args=[self.product.slug])), 'Internal Transport')

    def test_structured_data_cannot_close_script(self):
        self.product.name = 'Press </script><script>alert(1)</script>'; self.product.save()
        response = self.client.get(reverse('shop:product_detail', args=[self.product.slug]))
        scripts = re.findall(r'<script type="application/ld\+json">(.*?)</script>', response.content.decode(), re.S)
        script = next(value for value in scripts if json.loads(value).get('@type') == 'Product')
        data = json.loads(script)
        self.assertEqual(data['name'], self.product.name)
        self.assertNotIn('</script>', script)

    def test_package_checkout_discount_reservation_and_retry(self):
        cart = Cart.objects.create(session_key='package-test')
        CartPackage.objects.create(cart=cart, combo=self.combo, quantity=2)
        result = create_order_from_cart(cart, email='test@example.com')
        self.assertEqual(result.order.total, Decimal('360.00'))
        self.product.refresh_from_db(); self.assertEqual(self.product.reserved, 4)
        self.assertFalse(cart.packages.exists())
        with self.assertRaises(OrderCreationError):
            create_order_from_cart(cart, email='test@example.com')
        mark_order_as_paid(result.order); mark_order_as_paid(result.order)
        self.product.refresh_from_db(); self.assertEqual((self.product.quantity, self.product.reserved), (1, 0))

    def test_shared_component_demand_cannot_oversell(self):
        cart = Cart.objects.create(session_key='shared-demand')
        CartPackage.objects.create(cart=cart, combo=self.combo, quantity=2)
        cart.items.create(product=self.product, quantity=2)
        with self.assertRaises(OrderCreationError):
            create_order_from_cart(cart, email='test@example.com')
        self.product.refresh_from_db(); self.assertEqual(self.product.reserved, 0)

    def test_package_cart_whatsapp_and_remove(self):
        self.client.post('/shop/packages/add/', {'combo_id': self.combo.pk})
        response = self.client.get('/shop/cart/')
        message = parse_qs(urlparse(response.context['whatsapp_order_url']).query)['text'][0]
        self.assertIn('Starter Setup', message)
        self.assertIn('180.00', message)
        self.assertEqual(self.client.get('/shop/checkout/').status_code, 200)
        package = CartPackage.objects.get()
        self.client.post('/shop/packages/remove/', {'package_id': package.pk})
        self.assertFalse(CartPackage.objects.exists())

    def test_private_component_hides_package(self):
        self.product.is_public = False; self.product.save()
        self.assertNotContains(self.client.get('/shop/'), 'Starter Setup')
        self.assertEqual(self.client.post('/shop/packages/add/', {'combo_id': self.combo.pk}).status_code, 400)

    def test_sitemap_only_lists_public_products(self):
        response = self.client.get('/shop/sitemap-products.xml')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.product.slug)
        self.assertNotContains(response, self.hidden.slug)
        self.assertEqual(self.client.get('/shop/sitemap.xml').status_code, 200)

    def test_public_api_hides_internal_products(self):
        response = self.client.get('/inventory/products/', HTTP_HOST='api.boforg.co.zw')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Internal Transport')
        self.assertContains(response, 'Studio press')

    def test_add_event_is_not_repeated_on_refresh(self):
        self.client.post('/shop/cart/add/', {'product_id': self.product.pk})
        url = reverse('shop:product_detail', args=[self.product.slug])
        self.assertContains(self.client.get(url), 'add_to_cart')
        self.assertNotContains(self.client.get(url), 'add_to_cart')

    def test_product_browsing_does_not_create_cart(self):
        self.client.get(reverse('shop:product_detail', args=[self.product.slug]))
        self.assertFalse(Cart.objects.exists())

    def test_malformed_product_ids_are_bad_requests(self):
        for route in ['/shop/cart/add/', '/shop/cart/remove/']:
            self.assertEqual(self.client.post(route, {'product_id': 'invalid'}).status_code, 400)

    def test_async_add_returns_count_without_redirect_or_queued_flash(self):
        response = self.client.post('/shop/cart/add/', {'product_id': self.product.pk}, HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['cart_count'], 1)
        self.assertEqual(response.json()['message'], 'Studio press added to cart.')
        self.assertEqual(response.json()['analytics']['event'], 'add_to_cart')
        self.assertNotIn('store_events', self.client.session)
        self.assertNotIn('Location', response)

    def test_async_stock_failure_does_not_increment_cart(self):
        self.product.quantity = 0; self.product.save()
        response = self.client.post('/shop/cart/add/', {'product_id': self.product.pk}, HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 409)
        self.assertIn('Not enough stock', response.json()['message'])
        self.assertEqual(sum(cart.item_count for cart in Cart.objects.all()), 0)

    def test_async_package_add_returns_cart_count(self):
        response = self.client.post('/shop/packages/add/', {'combo_id': self.combo.pk}, HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['cart_count'], 1)

    def test_async_remove_updates_summary_and_empty_cart(self):
        self.client.post('/shop/cart/add/', {'product_id': self.product.pk}, HTTP_ACCEPT='application/json')
        response = self.client.post('/shop/cart/remove/', {'product_id': self.product.pk}, HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['cart_count'], 0)
        self.assertEqual(response.json()['message'], 'Studio press removed from cart.')
        self.assertIn('Your cart is empty.', response.json()['cart_html'])
        self.assertEqual(response.json()['analytics']['event'], 'remove_from_cart')
        self.assertNotIn('store_events', self.client.session)

    def test_async_package_removal_is_scoped_to_current_cart(self):
        other = Cart.objects.create(session_key='another-session')
        package = CartPackage.objects.create(cart=other, combo=self.combo)
        response = self.client.post('/shop/packages/remove/', {'package_id': package.pk}, HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(CartPackage.objects.filter(pk=package.pk).exists())
        self.assertNotIn('Starter Setup', response.json()['cart_html'])

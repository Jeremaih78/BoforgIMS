from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

from django.test import TestCase, override_settings
from django.urls import resolve

from inventory.models import Category, Combo, ComboItem, Product
from shop.models import Cart, Order
from .homepage import GOALS


@override_settings(BOFORG_WHATSAPP_NUMBER='263786264994')
class HomepageConversionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(name='Heatpress', department='printing')
        cls.press = Product.objects.create(name='Studio heat press', sku='WEB-PRESS', category=cls.category,
                                          price=300, quantity=5, reserved=1, image='products/test/press.jpg')
        cls.package = Combo.objects.create(name='First studio setup', code='web-setup', is_public=True,
                                          discount_type=Combo.DISCOUNT_FIXED, discount_value=25)
        ComboItem.objects.create(combo=cls.package, product=cls.press, quantity=1)

    def homepage(self, **params):
        return self.client.get('/', params, HTTP_HOST='boforg.co.zw', secure=True)

    def test_public_merchandising_uses_live_prices_and_real_stock(self):
        response = self.homepage()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Small start.')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(response.context['home_hero'], self.press)
        package = response.context['packages'][0]
        self.assertEqual(package.home_price, Decimal('275'))
        self.assertEqual(package.shop_available, 4)
        self.assertContains(response, 'Add this setup to cart')
        self.assertNotContains(response, 'earn from day one')
        self.assertNotContains(response, 'Profit Examples')

    def test_inactive_private_and_zero_price_products_are_never_promoted(self):
        for sku, fields in [('PRIVATE', {'is_public': False}), ('INACTIVE', {'is_active': False}), ('FREE', {'price': 0})]:
            Product.objects.create(name='Hidden heat press '+sku, sku=sku, category=self.category,
                                   quantity=10, image='products/test/private.jpg', **({'price': 100} | fields))
        response = self.homepage()
        self.assertEqual(response.context['home_hero'], self.press)
        self.assertEqual([p.pk for p in response.context['home_featured']], [self.press.pk])
        self.assertNotContains(response, 'Hidden heat press')

    def test_reserved_out_of_stock_equipment_has_no_purchase_claim(self):
        self.press.reserved = self.press.quantity
        self.press.save(update_fields=['reserved'])
        response = self.homepage()
        self.assertEqual(response.context['home_featured'], [])
        self.assertContains(response, 'Ask about availability')
        self.assertNotContains(response, 'Add this setup to cart')

    def test_private_component_hides_entire_package(self):
        self.press.is_public = False
        self.press.save(update_fields=['is_public'])
        response = self.homepage()
        self.assertEqual(response.context['packages'], [])
        self.assertContains(response, 'Your first setup')
        self.assertNotContains(response, self.package.name)

    def test_guide_works_on_server_without_javascript_and_qualifies_enquiry(self):
        response = self.homepage(goal='gifts', budget='500to800', experience='upgrade')
        self.assertEqual(response.context['guide_result']['title'], GOALS['gifts']['title'])
        url = urlsplit(response.context['guide_whatsapp'])
        self.assertEqual(url.netloc, 'wa.me')
        self.assertEqual(url.path, '/263786264994')
        message = parse_qs(url.query)['text'][0]
        self.assertIn('Mugs & personalised gifts', message)
        self.assertIn('USD 500–800', message)
        self.assertIn('Upgrading my current setup', message)
        self.assertContains(response, 'method="get"')
        self.assertNotIn('goal=', response.context['canonical_url'])

    def test_unknown_guide_input_cannot_be_reflected_or_injected(self):
        attack = '</script><script>alert(1)</script>'
        response = self.homepage(goal=attack, budget=attack, experience=attack)
        self.assertEqual(response.context['guide_goal'], 'explore')
        self.assertEqual(response.context['guide_budget'], 'unsure')
        self.assertEqual(response.context['guide_experience'], 'new')
        self.assertNotContains(response, attack)

    def test_cross_host_product_and_package_links_use_the_shop(self):
        response = self.homepage()
        self.assertEqual(urlsplit(response.context['home_hero'].home_url).netloc, 'shop.boforg.co.zw')
        self.assertContains(response, '//shop.boforg.co.zw/packages/add/')
        self.assertContains(response, '//shop.boforg.co.zw/cart/')

    def test_website_get_does_not_create_carts_orders_or_stock_movements(self):
        before = (Cart.objects.count(), Order.objects.count(), self.press.quantity, self.press.reserved)
        self.homepage(goal='shirts', budget='1500plus')
        self.press.refresh_from_db()
        after = (Cart.objects.count(), Order.objects.count(), self.press.quantity, self.press.reserved)
        self.assertEqual(before, after)

    def test_null_category_and_empty_catalogue_render_without_failure(self):
        self.press.category = None
        self.press.save(update_fields=['category'])
        self.assertEqual(self.homepage().status_code, 200)
        self.press.is_active = False
        self.press.save(update_fields=['is_active'])
        response = self.homepage()
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['home_hero'])
        self.assertContains(response, '/static/img/1.jpeg')

    def test_shared_shop_layout_retains_its_navigation_and_cart(self):
        response = self.client.get('/', HTTP_HOST='shop.boforg.co.zw', secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="storefront"')
        self.assertContains(response, 'id="store-navigation"')
        self.assertNotContains(response, 'class="bh-header"')

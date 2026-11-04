from decimal import Decimal
from io import BytesIO
from pathlib import Path
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.forms import modelform_factory
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from inventory.forms import ProductForm
from inventory.models import Product


class ProductAdminTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        media = tempfile.TemporaryDirectory(prefix='boforg-product-admin-')
        assert Path(media.name).resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())
        cls.addClassCleanup(media.cleanup)
        cls.media_settings = override_settings(MEDIA_ROOT=media.name)
        cls.media_settings.enable()
        cls.addClassCleanup(cls.media_settings.disable)

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser('product-admin', '', 'synthetic-password')
        cls.product = Product.objects.create(
            name='Original product', sku='ADMIN-ORIGINAL', price=Decimal('100.00'),
            quantity=7, reserved=2, avg_cost=Decimal('12.5000'),
            tracking_mode=Product.TRACK_QUANTITY,
        )

    def setUp(self):
        self.client.force_login(self.user)

    def url(self, product=None):
        return reverse('admin:inventory_product_change', args=[(product or self.product).pk])

    def payload(self, **changes):
        data = {
            'name': 'Updated product', 'sku': self.product.sku, 'slug': '',
            'category': '', 'supplier': '', 'price': '125.75', 'currency': 'USD',
            'track_inventory': 'on', 'tracking_mode': Product.TRACK_QUANTITY,
            'reorder_level': '3', 'tax_rate': '0', 'description': 'Updated description',
            'short_description': 'Updated summary', 'is_public': 'on', 'is_active': 'on',
            '_save': 'Save',
        }
        data.update(changes)
        return data

    def image(self, name='product.png', color='blue'):
        content = BytesIO()
        Image.new('RGB', (16, 16), color=color).save(content, format='PNG')
        return SimpleUploadedFile(name, content.getvalue(), content_type='image/png')

    def assert_stock_unchanged(self, product=None):
        product = product or self.product
        product.refresh_from_db()
        self.assertEqual((product.quantity, product.reserved, product.avg_cost),
                         (7, 2, Decimal('12.5000')))

    def test_existing_product_admin_pages_open_with_and_without_images(self):
        uploaded = Product.objects.create(name='Uploaded image', sku='ADMIN-IMAGE', price=10)
        uploaded.image.save('original.png', self.image(), save=True)
        legacy = Product.objects.create(name='Legacy image', sku='ADMIN-LEGACY', price=10,
                                        image_url='https://example.invalid/legacy.png')
        serial = Product.objects.create(name='Serial product', sku='ADMIN-SERIAL', price=10,
                                        tracking_mode=Product.TRACK_SERIAL)
        for product in (self.product, uploaded, legacy, serial):
            with self.subTest(product=product.sku):
                response = self.client.get(self.url(product))
                self.assertEqual(response.status_code, 200)
                form = response.context['adminform'].form
                for field in ('quantity', 'reserved', 'avg_cost'):
                    self.assertNotIn(field, form.fields)

    def test_admin_metadata_edit_saves_and_preserves_stock(self):
        response = self.client.post(self.url(), self.payload())
        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, 'Updated product')
        self.assertEqual(self.product.price, Decimal('125.75'))
        self.assertEqual(self.product.short_description, 'Updated summary')
        self.assert_stock_unchanged()

    def test_admin_ignores_submitted_stock_quantity_reservation_and_cost(self):
        response = self.client.post(self.url(), self.payload(quantity='999', reserved='998', avg_cost='999.99'))
        self.assertEqual(response.status_code, 302)
        self.assert_stock_unchanged()

    def test_admin_image_upload_saves_valid_file(self):
        response = self.client.post(self.url(), self.payload(image=self.image()))
        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        self.assertTrue(self.product.image)
        self.assertTrue(Path(self.product.image.path).is_file())
        with Image.open(self.product.image.path) as image:
            self.assertEqual(image.size, (16, 16))
        self.assert_stock_unchanged()

    def test_admin_preserves_existing_image_when_editing_metadata(self):
        self.product.image.save('existing.png', self.image(), save=True)
        name = self.product.image.name
        content = Path(self.product.image.path).read_bytes()
        response = self.client.post(self.url(), self.payload())
        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.image.name, name)
        self.assertEqual(Path(self.product.image.path).read_bytes(), content)
        self.assert_stock_unchanged()

    def test_admin_replacement_image_saves(self):
        self.product.image.save('before.png', self.image(), save=True)
        response = self.client.post(self.url(), self.payload(image=self.image('after.png', 'red')))
        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        with Image.open(self.product.image.path) as image:
            self.assertEqual(image.convert('RGB').getpixel((0, 0)), (255, 0, 0))
        self.assert_stock_unchanged()

    def test_admin_invalid_image_does_not_save_product_changes(self):
        invalid = SimpleUploadedFile('invalid.png', b'not an image', content_type='image/png')
        response = self.client.post(self.url(), self.payload(image=invalid))
        self.assertEqual(response.status_code, 200)
        self.assertIn('image', response.context['adminform'].form.errors)
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, 'Original product')
        self.assertFalse(self.product.image)
        self.assert_stock_unchanged()

    def test_admin_add_page_and_creation_keep_stock_fields_protected(self):
        url = reverse('admin:inventory_product_add')
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url, self.payload(sku='ADMIN-NEW', quantity='999', reserved='998', avg_cost='999.99'))
        self.assertEqual(response.status_code, 302)
        product = Product.objects.get(sku='ADMIN-NEW')
        self.assertEqual((product.quantity, product.reserved, product.avg_cost), (0, 0, Decimal('0')))

    def test_staff_without_product_permissions_cannot_edit(self):
        staff = get_user_model().objects.create_user('restricted-admin', password='synthetic-password', is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(self.url()).status_code, 403)
        self.assertEqual(self.client.post(self.url(), self.payload()).status_code, 403)
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, 'Original product')
        self.assert_stock_unchanged()

    def test_standard_product_form_still_disables_stock_and_saves_metadata(self):
        form = ProductForm(instance=self.product, data=self.payload(quantity='999', reserved='998', avg_cost='999.99'))
        for field in ('quantity', 'reserved', 'avg_cost'):
            self.assertTrue(form.fields[field].disabled)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, 'Updated product')
        self.assert_stock_unchanged()

    def test_restricted_product_form_can_edit_without_stock_fields(self):
        form_class = modelform_factory(Product, form=ProductForm, fields=['name', 'sku'])
        form = form_class(instance=self.product, data={'name': 'Restricted edit', 'sku': self.product.sku})
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, 'Restricted edit')
        self.assert_stock_unchanged()

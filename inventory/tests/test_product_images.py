import os
import shutil
import tempfile
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image
from rest_framework.test import APIRequestFactory

from inventory.forms import ProductForm
from inventory.models import Product
from inventory.serializers import ProductSerializer


TEST_MEDIA_ROOT = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class ProductImageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username='product-tester',
            email='tester@example.com',
            password='strong-password',
            is_staff=True,
            is_superuser=True,
        )

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def _base_form_data(self):
        return {
            'name': 'Sample Product',
            'sku': 'SKU-001',
            'price': '19.99',
            'quantity': 5,
            'currency': 'USD',
            'avg_cost': '10.00',
            'reserved': 0,
            'track_inventory': 'on',
            'reorder_level': 0,
            'tax_rate': '0',
            'description': 'Test description',
            'is_active': 'on',
        }

    def _make_image_file(self, name='test.png', size=(64, 64), format='PNG', color='blue'):
        buffer = BytesIO()
        Image.new('RGB', size, color=color).save(buffer, format=format)
        return SimpleUploadedFile(name, buffer.getvalue(), content_type=f'image/{format.lower()}')

    def test_create_product_with_image_upload(self):
        self.client.force_login(self.user)
        image_file = self._make_image_file()
        form_data = self._base_form_data()
        form_data['sku'] = 'SKU-UPLOAD'
        response = self.client.post(
            reverse('ims:inventory:product_create'),
            {**form_data, 'image': image_file},
        )
        self.assertEqual(response.status_code, 302)
        product = Product.objects.get(sku='SKU-UPLOAD')
        self.assertTrue(product.image)
        self.assertTrue(os.path.exists(product.image.path))
        self.assertIn('products/SKU-UPLOAD', product.image.name)

    def test_product_form_rejects_non_image_file(self):
        file_obj = SimpleUploadedFile('not-image.txt', b'hello world', content_type='text/plain')
        form = ProductForm(data=self._base_form_data(), files={'image': file_obj})
        self.assertFalse(form.is_valid())
        self.assertIn('image', form.errors)
        self.assertIn('valid image', form.errors['image'][0])

    def test_product_form_rejects_large_file(self):
        large_file = self._make_image_file(name='big.bmp', size=(4000, 4000), format='BMP', color='green')
        self.assertGreater(large_file.size, 5 * 1024 * 1024)
        form = ProductForm(data=self._base_form_data(), files={'image': large_file})
        self.assertFalse(form.is_valid())
        self.assertIn('5MB', form.errors['image'][0])

    def test_serializer_provides_image_url_and_fallback(self):
        product_with_image = Product.objects.create(
            name='Image Product',
            sku='IMG-001',
            price=9.99,
            quantity=1,
            currency='USD',
        )
        image_file = self._make_image_file()
        product_with_image.image.save('sample.png', ContentFile(image_file.read()), save=True)

        legacy_product = Product.objects.create(
            name='Legacy Product',
            sku='LEG-001',
            price=5.00,
            quantity=1,
            currency='USD',
            image_url='https://cdn.example.com/legacy.png',
        )

        request = APIRequestFactory().get('/')
        serializer = ProductSerializer(product_with_image, context={'request': request})
        self.assertTrue(serializer.data['image'].startswith('http://testserver/media/products/'))

        legacy_serializer = ProductSerializer(legacy_product, context={'request': request})
        self.assertEqual(legacy_serializer.data['image'], legacy_product.image_url)

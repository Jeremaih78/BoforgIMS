"""Isolated local PostgreSQL settings. Never use this module for deployment."""
import os

os.environ['DJANGO_DEBUG'] = 'true'
os.environ['DJANGO_SECRET_KEY'] = 'isolated-test-key-only-not-for-deployment'

from .settings import *  # noqa: F403,E402

DATABASES = {'default': {
    'ENGINE': 'django.db.backends.postgresql',
    'NAME': 'ims_review',
    'USER': 'ims_review',
    'PASSWORD': '',
    'HOST': '127.0.0.1',
    'PORT': os.environ.get('IMS_TEST_PORT', '55439'),
}}
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
TASKER_AI_OPENAI_API_KEY = ''
SESSION_COOKIE_DOMAIN = None
CSRF_COOKIE_DOMAIN = None
ALLOWED_HOSTS = ['testserver', 'localhost', '127.0.0.1', *BOFORG_PUBLIC_HOSTS]  # noqa: F405
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}

PRIVATE_MEDIA_ROOT = BASE_DIR / ".local-review" / "private-test-media"  # noqa: F405

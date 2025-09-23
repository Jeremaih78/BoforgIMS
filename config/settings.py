
import os
from pathlib import Path
from dotenv import load_dotenv
import dj_database_url

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("SECRET_KEY")
DEBUG = os.environ.get("DEBUG", "False").lower() == "true"
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "http://72.60.20.46:8000").split(" ")

# CSRF_TRUSTED_ORIGINS must include scheme (https://)
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("CSRF_TRUSTED_ORIGINS", "https://boforgims1.onrender.com").split(",")
    if origin.strip()
]

# Trust the proxy header to detect HTTPS (Render/most PaaS terminate TLS)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Local apps
    'inventory',
    'sales',
    'customers',
    'users',
    'accounting',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# DATABASES["default"] = dj_database_url.parse("postgresql://boforg_ims_user:FCj0L3Xf5pWnrAvzQiZEF1xmO4lHpC9n@dpg-d36255ali9vc738s4bd0-a.oregon-postgres.render.com/boforg_ims")

# database_url = os.environ.get("DATABASE_URL")
# DATABASES = {
#     "default": dj_database_url.parse(
#         database_url,
#         conn_max_age=60,
#     )
# }

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('POSTGRES_DB', 'boforg_ims'),
        'USER': os.environ.get('POSTGRES_USER', 'boforg'),
        'PASSWORD': os.environ.get('POSTGRES_PASSWORD', 'boforg2024'),
        'HOST': os.environ.get('POSTGRES_HOST', 'localhost'),
        'PORT': os.environ.get('POSTGRES_PORT', '5432'),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Africa/Harare'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Accounting settings
BASE_CURRENCY_CODE = os.environ.get("BASE_CURRENCY_CODE", "USD")
ACCOUNTING_COGS_METHOD = os.environ.get("ACCOUNTING_COGS_METHOD", "MOVING_AVERAGE")
ACCOUNTING_POST_COGS_ON = os.environ.get("ACCOUNTING_POST_COGS_ON", "PAYMENT")
DEFAULT_TAX_RATE_ID = os.environ.get("DEFAULT_TAX_RATE_ID")
PERIOD_CLOSE_ENFORCED = os.environ.get("PERIOD_CLOSE_ENFORCED", "1") == "1"

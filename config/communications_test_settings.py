"""Isolated consumer test database; connection credentials are supplied by the test runner."""

import os
from .test_settings import *  # noqa: F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "ims_review",
        "USER": os.environ["IMS_TEST_DB_USER"],
        "PASSWORD": os.environ["IMS_TEST_DB_PASSWORD"],
        "HOST": "127.0.0.1",
        "PORT": os.environ.get("IMS_TEST_DB_PORT", "55439"),
        "TEST": {"NAME": "test_ims_cloud_communications"},
    }
}
CLOUD_COMMUNICATIONS_ENABLED = True
CLOUD_COMMUNICATIONS_CURRENCY = "USD"

"""Disposable local PostgreSQL only. Use tools/test_staff_email.py to block delivery."""

import os
import tempfile
from pathlib import Path
from .test_settings import *  # noqa: F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "staff_email_ims",
        "USER": os.environ.get("STAFF_EMAIL_TEST_USER", "integration_test"),
        "PASSWORD": os.environ.get("STAFF_EMAIL_TEST_PASSWORD", ""),
        "HOST": "127.0.0.1",
        "PORT": os.environ.get("STAFF_EMAIL_TEST_PORT", "55457"),
        "TEST": {"NAME": "test_staff_email_ims"},
    }
}
PRIVATE_MEDIA_ROOT = Path(tempfile.gettempdir()) / "boforg-staff-email-synthetic-media"
TASKER_CLOUD_EMAIL_ENABLED = False
TASKER_CLOUD_EMAIL_CLIENTS = {}
TASKER_CLOUD_EMAIL_KEYS = {}
TASKER_CLOUD_EMAIL_RECEIPT_KEYS = {}
TASKER_EMAIL_ORIGIN = "https://ims.example.invalid"
TASKER_REPORT_EMAIL_LIVE_ENABLED = False

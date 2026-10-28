"""Exercise the production asset pipeline without a database or provider call."""

import io
import json
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.test import SimpleTestCase, override_settings


class ProductionVendorStaticTests(SimpleTestCase):
    def test_all_assets_collect_with_strict_compressed_manifest_storage(self):
        storage_settings = {
            "default": settings.STORAGES["default"],
            "staticfiles": {
                "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
            },
        }
        with tempfile.TemporaryDirectory(prefix="ims-static-manifest-") as target:
            with override_settings(
                DEBUG=False, STATIC_ROOT=target, STORAGES=storage_settings,
            ):
                call_command(
                    "collectstatic", interactive=False, verbosity=0,
                    stdout=io.StringIO(), stderr=io.StringIO(),
                )
                manifest = json.loads((Path(target) / "staticfiles.json").read_text())
                for source in (
                    "tasker/vendor/bootstrap/js/bootstrap.bundle.min.js",
                    "tasker/vendor/bootstrap/js/bootstrap.bundle.min.js.map",
                    "tasker/vendor/bootstrap/css/bootstrap.min.css",
                    "tasker/vendor/bootstrap/css/bootstrap.min.css.map",
                ):
                    with self.subTest(asset=source):
                        hashed = manifest["paths"][source]
                        self.assertTrue((Path(target) / hashed).is_file())

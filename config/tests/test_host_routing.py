from urllib.parse import urlparse

from django.contrib.auth.models import AnonymousUser
from django.test.client import RequestFactory
from django.test import SimpleTestCase, override_settings
from django.template.loader import render_to_string
from django.urls import Resolver404, resolve, reverse
from django_hosts.resolvers import reverse as host_reverse

from shop.views import catalog
from users.views import dashboard


PUBLIC_HOSTS = [
    "boforg.co.zw",
    "www.boforg.co.zw",
    "shop.boforg.co.zw",
    "ims.boforg.co.zw",
    "ai.boforg.co.zw",
    "api.boforg.co.zw",
    "testserver",
]


@override_settings(ALLOWED_HOSTS=PUBLIC_HOSTS)
class HostRoutingTests(SimpleTestCase):
    def setUp(self):
        self.request_factory = RequestFactory()

    def render_for_host(self, template_name, urlconf, context=None, user=None):
        request = self.request_factory.get("/")
        request.user = user or AnonymousUser()
        request.urlconf = urlconf
        with override_settings(ROOT_URLCONF=urlconf):
            return render_to_string(template_name, context or {}, request=request)

    def test_each_host_selects_only_its_urlconf(self):
        expectations = {
            "boforg.co.zw": "config.host_urls.website",
            "www.boforg.co.zw": "config.host_urls.website",
            "shop.boforg.co.zw": "config.host_urls.shop",
            "ims.boforg.co.zw": "config.host_urls.ims",
            "ai.boforg.co.zw": "config.host_urls.ai",
            "api.boforg.co.zw": "config.host_urls.api",
        }

        for hostname, urlconf in expectations.items():
            with self.subTest(hostname=hostname):
                response = self.client.get("/definitely-not-a-route/", HTTP_HOST=hostname)
                self.assertEqual(response.wsgi_request.urlconf, urlconf)

    def test_unregistered_subdomain_is_closed(self):
        with override_settings(ALLOWED_HOSTS=[*PUBLIC_HOSTS, "future.boforg.co.zw"]):
            response = self.client.get("/", HTTP_HOST="future.boforg.co.zw")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.wsgi_request.urlconf,
            "config.host_urls.not_found",
        )

    def test_testserver_keeps_legacy_path_router(self):
        response = self.client.get("/ims/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])
        self.assertEqual(response.wsgi_request.urlconf, "config.urls")

    def test_shop_is_rooted_at_shop_host(self):
        match = resolve("/", urlconf="config.host_urls.shop")
        self.assertIs(match.func, catalog)
        self.assertEqual(
            reverse("shop:cart_detail", urlconf="config.host_urls.shop"),
            "/cart/",
        )
        with self.assertRaises(Resolver404):
            resolve("/ims/", urlconf="config.host_urls.shop")

    def test_ims_is_rooted_at_ims_host_without_tasker(self):
        match = resolve("/", urlconf="config.host_urls.ims")
        self.assertIs(match.func, dashboard)
        self.assertEqual(
            reverse("ims:inventory:product_list", urlconf="config.host_urls.ims"),
            "/inventory/",
        )
        with self.assertRaises(Resolver404):
            resolve("/tasker/", urlconf="config.host_urls.ims")

    def test_ai_preserves_tasker_namespace_at_host_root(self):
        self.assertEqual(
            reverse("ims:tasker:index", urlconf="config.host_urls.ai"),
            "/",
        )
        self.assertEqual(
            reverse("ims:tasker:assistant_home", urlconf="config.host_urls.ai"),
            "/ai/assistant/",
        )
        with self.assertRaises(Resolver404):
            resolve("/inventory/", urlconf="config.host_urls.ai")

    def test_api_host_exposes_api_only(self):
        response = self.client.get("/", HTTP_HOST="api.boforg.co.zw")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["name"], "Boforg API")
        self.assertEqual(
            reverse("product-list", urlconf="config.host_urls.api"),
            "/inventory/products/",
        )
        with self.assertRaises(Resolver404):
            resolve("/shop/", urlconf="config.host_urls.api")

    def test_api_product_writes_require_authentication(self):
        response = self.client.post(
            "/inventory/products/",
            {"name": "Unauthorized product"},
            HTTP_HOST="api.boforg.co.zw",
        )
        self.assertEqual(response.status_code, 403)

    def test_cross_host_reverse_generation(self):
        urls = {
            "website": host_reverse("website:home", host="website"),
            "shop": host_reverse("shop:catalog", host="shop"),
            "ims": host_reverse("ims:dashboard", host="ims"),
            "ai": host_reverse("ims:tasker:index", host="ai"),
            "api": host_reverse("api_index", host="api"),
        }
        self.assertEqual(urlparse(urls["website"]).netloc, "boforg.co.zw")
        for name in ("shop", "ims", "ai", "api"):
            with self.subTest(name=name):
                self.assertEqual(urlparse(urls[name]).netloc, f"{name}.boforg.co.zw")
                self.assertEqual(urlparse(urls[name]).path, "/")

    def test_shared_navigation_renders_on_ims_and_ai_hosts(self):
        class PermittedUser:
            is_authenticated = True
            is_staff = True
            is_superuser = True
            username = "host-routing-test"

            def has_perm(self, permission):
                return True

            def has_module_perms(self, app_label):
                return True

        user = PermittedUser()
        ims_html = self.render_for_host("base.html", "config.host_urls.ims", user=user)
        ai_html = self.render_for_host("base.html", "config.host_urls.ai", user=user)

        self.assertIn("//ims.boforg.co.zw/", ims_html)
        self.assertIn("//ai.boforg.co.zw/", ims_html)
        self.assertIn("//ims.boforg.co.zw/", ai_html)
        self.assertIn("//ai.boforg.co.zw/", ai_html)

    def test_shop_templates_use_host_root_paths(self):
        html = self.render_for_host(
            "shop/catalog.html",
            "config.host_urls.shop",
            {"categories": [], "products": []},
        )
        self.assertIn('href="/cart/"', html)
        self.assertNotIn('href="/shop/', html)

    def test_legacy_public_paths_redirect_to_canonical_hosts(self):
        response = self.client.get("/shop/cart/?source=legacy", HTTP_HOST="boforg.co.zw")
        self.assertEqual(response.status_code, 308)
        self.assertEqual(
            response["Location"],
            "http://shop.boforg.co.zw/cart/?source=legacy",
        )

        response = self.client.get("/ims/tasker/tasks/", HTTP_HOST="boforg.co.zw")
        self.assertEqual(response.status_code, 308)
        self.assertEqual(response["Location"], "http://ai.boforg.co.zw/tasks/")

    def test_legacy_aggregate_urlconf_remains_available(self):
        self.assertEqual(reverse("shop:catalog"), "/shop/")
        self.assertEqual(reverse("ims:dashboard"), "/ims/")

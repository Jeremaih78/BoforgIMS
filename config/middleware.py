"""Project-level integration points for hostname routing."""

from django.conf import settings
from django.http.request import split_domain_port
from django.urls import set_urlconf
from django_hosts.middleware import HostsRequestMiddleware, HostsResponseMiddleware


LOCAL_LEGACY_HOSTS = frozenset({"testserver", "localhost", "127.0.0.1", "::1"})


def _uses_legacy_urlconf(request):
    """Keep the established path router for local development and Django tests."""
    domain, _port = split_domain_port(request.get_host())
    return domain.lower() in LOCAL_LEGACY_HOSTS


class BoforgHostsRequestMiddleware(HostsRequestMiddleware):
    def process_request(self, request):
        if _uses_legacy_urlconf(request):
            request.urlconf = settings.ROOT_URLCONF
            return None
        return super().process_request(request)


class BoforgHostsResponseMiddleware(HostsResponseMiddleware):
    def process_response(self, request, response):
        if _uses_legacy_urlconf(request):
            request.urlconf = settings.ROOT_URLCONF
            set_urlconf(settings.ROOT_URLCONF)
            return response
        return super().process_response(request, response)

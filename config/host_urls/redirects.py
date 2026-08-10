"""Backwards-compatible redirects from legacy path-based public URLs."""

from django.http.response import HttpResponseRedirectBase
from django.utils.encoding import iri_to_uri
from django.utils.http import escape_leading_slashes
from django_hosts.resolvers import reverse_host


class PermanentRedirect308(HttpResponseRedirectBase):
    status_code = 308


def legacy_host_redirect(request, host_name, path=""):
    """Move an old path to its canonical application host without losing POST."""
    safe_path = escape_leading_slashes(path)
    target = f"{request.scheme}://{reverse_host(host_name)}/{safe_path}"
    query_string = request.META.get("QUERY_STRING")
    if query_string:
        target = f"{target}?{query_string}"
    return PermanentRedirect308(iri_to_uri(target))

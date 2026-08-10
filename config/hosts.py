"""Hostname-to-URLConf routing for the Boforg platform."""

from django_hosts import host, patterns


host_patterns = patterns(
    "",
    host(r"", "config.host_urls.website", name="website"),
    host(r"www", "config.host_urls.website", name="www"),
    host(r"shop", "config.host_urls.shop", name="shop"),
    host(r"ims", "config.host_urls.ims", name="ims"),
    host(r"ai", "config.host_urls.ai", name="ai"),
    host(r"api", "config.host_urls.api", name="api"),
    host(r".+", "config.host_urls.not_found", name="unmatched"),
)

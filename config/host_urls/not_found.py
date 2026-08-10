"""Closed URLConf for allowed hosts that do not have an application."""

from django.http import Http404
from django.urls import path


def host_not_found(request, path=""):
    raise Http404("No Boforg application is configured for this hostname.")


urlpatterns = [
    path("", host_not_found),
    path("<path:path>", host_not_found),
]

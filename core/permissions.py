"""Backend permissions for the documented IMS roles and explicit Django grants."""
from functools import wraps

from django.core.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission, SAFE_METHODS


def allowed(user, permission, *, staff=False):
    if not user.is_authenticated or not user.is_active:
        return False
    if user.has_perm(permission):
        return True
    roles = {'Admin', 'Staff'} if staff else {'Admin'}
    return user.groups.filter(name__in=roles).exists()


def ims_permission(permission, *, staff=False, write_permission=None):
    def decorate(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            required = write_permission if write_permission and request.method not in SAFE_METHODS else permission
            if not allowed(request.user, required, staff=staff):
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return wrapped
    return decorate


class CataloguePermission(BasePermission):
    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        action = {'POST': 'add', 'PUT': 'change', 'PATCH': 'change', 'DELETE': 'delete'}.get(request.method)
        return bool(action and allowed(request.user, f'inventory.{action}_product', staff=action != 'delete'))


class ShipmentPermission(BasePermission):
    def has_permission(self, request, view):
        action = 'view' if request.method in SAFE_METHODS else 'change'
        return allowed(request.user, f'inventory.{action}_shipment')


class InvoiceSerialPermission(BasePermission):
    def has_permission(self, request, view):
        action = 'view' if request.method in SAFE_METHODS else 'change'
        return allowed(request.user, f'sales.{action}_invoice', staff=True)

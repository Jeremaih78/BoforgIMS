from django.urls import path

from .views import (
    DataDeletionView,
    PrivacyView,
    TermsView,
    facebook_data_deletion,
)


app_name = "legal"

urlpatterns = [
    path("privacy/", PrivacyView.as_view(), name="privacy"),
    path("terms/", TermsView.as_view(), name="terms"),
    path("data-deletion/", DataDeletionView.as_view(), name="data_deletion"),
    path(
        "facebook-data-deletion/",
        facebook_data_deletion,
        name="fb_data_deletion",
    ),
]

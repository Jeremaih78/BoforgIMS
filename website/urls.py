from django.urls import path

from .views import HomeView, NewsletterSubscribeView

app_name = 'website'

urlpatterns = [
    path('', HomeView.as_view(), name='home'),
    path('newsletter/subscribe/', NewsletterSubscribeView.as_view(), name='newsletter_subscribe'),
]

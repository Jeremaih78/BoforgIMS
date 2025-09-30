from django.urls import include, path

from users.views import dashboard

app_name = 'ims'

urlpatterns = [
    path('', dashboard, name='dashboard'),
    path('accounts/', include('django.contrib.auth.urls')),
    path('inventory/', include(('inventory.urls', 'inventory'), namespace='inventory')),
    path('customers/', include(('customers.urls', 'customers'), namespace='customers')),
    path('sales/', include(('sales.urls', 'sales'), namespace='sales')),
    path('accounting/', include(('accounting.urls', 'accounting'), namespace='accounting')),
    path('legal/', include(('legal.urls', 'legal'), namespace='legal')),
]

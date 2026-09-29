from django.urls import include, path

from users.views import dashboard
from core.views import private_document

app_name = 'ims'

urlpatterns = [
    path('documents/<path:name>', private_document, name='private_document'),
    path('', dashboard, name='dashboard'),
    path('accounts/', include('django.contrib.auth.urls')),
    path('inventory/', include(('inventory.urls', 'inventory'), namespace='inventory')),
    path('customers/', include(('customers.urls', 'customers'), namespace='customers')),
    path('sales/', include(('sales.urls', 'sales'), namespace='sales')),
    path('accounting/', include(('accounting.urls', 'accounting'), namespace='accounting')),
    path('credit-control/', include(('credit_control.urls', 'credit_control'), namespace='credit_control')),
    path('tasker/', include(('tasker.urls', 'tasker'), namespace='tasker')),
    path('legal/', include(('legal.urls', 'legal'), namespace='legal')),
]

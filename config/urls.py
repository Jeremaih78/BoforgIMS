from django.contrib import admin
from django.urls import include, path
from legal.views import return_policy
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path("return-policy/", return_policy, name="return_policy"),
    path('', include(('website.urls', 'website'), namespace='website')),
    path('shop/', include(('shop.urls', 'shop'), namespace='shop')),
    path('legal/', include(('legal.urls', 'legal'), namespace='legal')),
    path('ims/', include(('ims.urls', 'ims'), namespace='ims')),
    path('accounts/', include('django.contrib.auth.urls')),
    path('admin/', admin.site.urls),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    



from config.host_urls.common import health_patterns
urlpatterns=health_patterns()+urlpatterns

handler400="core.errors.bad_request"
handler403="core.errors.forbidden"
handler404="core.errors.not_found"
handler500="core.errors.server_error"

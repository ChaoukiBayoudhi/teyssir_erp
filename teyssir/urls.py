from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from teyssir.core.views import health

urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health, name="health"),
    path("api/v1/sync/", include("teyssir.sync.urls")),
    path("api/v1/", include("teyssir.api.urls")),
]

if settings.DEBUG:  # serve uploaded images locally in dev (Caddy serves /media in prod)
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

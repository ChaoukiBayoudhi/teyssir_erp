from django.contrib import admin
from django.urls import include, path

from teyssir.core.views import health

urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health, name="health"),
    path("api/v1/sync/", include("teyssir.sync.urls")),
    path("api/v1/", include("teyssir.api.urls")),
]

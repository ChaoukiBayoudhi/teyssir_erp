"""Hub-side sync endpoints (spec §4.4).

Machine-to-machine: a till authenticates with a shared `X-Sync-Key` header (not a user
session), so we disable session auth/CSRF on these views and gate them with a key check.
A proper per-terminal credential is a Phase-1 hardening item (spec §18).
"""
from django.conf import settings
from django.utils.dateparse import parse_datetime
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from .services import apply_push, collect_master_changes


class HasSyncKey(BasePermission):
    message = "Missing or invalid X-Sync-Key."

    def has_permission(self, request, view):
        key = settings.SYNC_KEY
        return bool(key) and request.headers.get("X-Sync-Key") == key


class SyncPushView(APIView):
    authentication_classes = []
    permission_classes = [HasSyncKey]

    def post(self, request):
        entries = request.data.get("entries", [])
        return Response(apply_push(entries))


class SyncPullView(APIView):
    authentication_classes = []
    permission_classes = [HasSyncKey]

    def get(self, request):
        since_raw = request.query_params.get("since")
        since = parse_datetime(since_raw) if since_raw else None
        return Response(collect_master_changes(since))

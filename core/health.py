"""Small, read-only health probes; never expose configuration or exceptions."""
import logging
from uuid import uuid4
from django.core.cache import cache
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.http import JsonResponse
from django.views.decorators.http import require_GET

_schema_key='ims-schema-ready-'+uuid4().hex
logger=logging.getLogger(__name__)


@require_GET
def live(request):
    response=JsonResponse({'status':'ok'})
    response['Cache-Control']='no-store'
    return response


def database_ready():
    try:
        with connection.cursor() as cursor:cursor.execute('SELECT 1');cursor.fetchone()
        if not cache.get(_schema_key):
            executor=MigrationExecutor(connection)
            executor.loader.check_consistent_history(connection)
            if executor.loader.detect_conflicts() or executor.migration_plan(executor.loader.graph.leaf_nodes()):return False
            cache.set(_schema_key,True,5)
        return True
    except Exception:
        logger.warning('IMS readiness probe failed; check database connectivity and migrations.')
        return False


@require_GET
def ready(request):
    ok=database_ready()
    response=JsonResponse({'status':'ready' if ok else 'unavailable'},status=200 if ok else 503)
    response['Cache-Control']='no-store'
    return response

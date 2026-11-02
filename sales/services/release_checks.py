"""Read-only release evidence. No repairs, migrations, providers or deliveries."""
from importlib.metadata import version,PackageNotFoundError
from pathlib import Path
import re
from django.conf import settings
from django.core.checks import run_checks,WARNING,ERROR
from django.contrib.staticfiles.storage import staticfiles_storage
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from .integrity import integrity_report

ASSETS=('admin/css/base.css','css/app.css','css/money.css','css/ims_experience.css',
    'js/document_composer.js','js/money_math.js','js/money_capture.js','js/searchable_select.js',
    'tasker/css/ui.css','tasker/vendor/bootstrap/css/bootstrap.min.css',
    'tasker/vendor/bootstrap/js/bootstrap.bundle.min.js','tasker/vendor/bootstrap-icons/font/bootstrap-icons.css')


def release_report(*,production=False):
    checks={};details={}
    mismatches=[]
    for line in (Path(settings.BASE_DIR)/'requirements.txt').read_text(encoding='utf-8').splitlines():
        match=re.fullmatch(r'([A-Za-z0-9_.-]+)==([^\s]+)',line.strip())
        if not match:continue
        package,expected=match.groups()
        try:actual=version(package)
        except PackageNotFoundError:actual='missing'
        if actual!=expected:mismatches.append({'package':package,'expected':expected,'installed':actual})
    checks['pinned_dependencies']=not mismatches;details['dependency_mismatches']=mismatches
    messages=run_checks(include_deployment_checks=production)
    rejected=[message.id for message in messages if message.level>=(WARNING if production else ERROR)]
    checks['django_configuration']=not rejected;details['django_check_ids']=rejected
    checks['postgresql']=connection.vendor=='postgresql'
    try:
        executor=MigrationExecutor(connection);executor.loader.check_consistent_history(connection)
        pending=executor.migration_plan(executor.loader.graph.leaf_nodes())
        checks['schema']=not pending and not executor.loader.detect_conflicts()
        details['pending_migrations']=[f'{m.app_label}.{m.name}' for m,backward in pending]
    except Exception:
        checks['schema']=False;details['database_error']='Database connectivity or migration history requires review.'
    findings={}
    if checks['schema']:
        report=integrity_report()
        findings={key:len(value) for key,value in report.items() if key!='note' and value}
    checks['stock_and_ledger_integrity']=checks['schema'] and not findings
    details['reconciliation_findings']=findings
    if production:
        checks['production_mode']=not settings.DEBUG
        failures=[]
        try:
            for asset in ASSETS:
                stored=staticfiles_storage.stored_name(asset)
                if stored==asset or not staticfiles_storage.exists(stored):failures.append(asset)
        except Exception:
            failures.append('Static manifest could not be validated. Run collectstatic with production settings.')
        checks['hashed_static_assets']=not failures;details['static_failures']=failures
        private=Path(settings.PRIVATE_MEDIA_ROOT).resolve()
        public=[Path(settings.MEDIA_ROOT).resolve(),Path(settings.STATIC_ROOT).resolve(),*[Path(p).resolve() for p in settings.STATICFILES_DIRS if isinstance(p,(str,Path))]]
        checks['private_upload_isolation']=not any(private==p or private.is_relative_to(p) for p in public)
    return {'ready':all(checks.values()),'production_checks':production,'checks':checks,'details':details,'database_write':False,'provider_request':False}

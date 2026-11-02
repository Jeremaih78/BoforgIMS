import json
from django.core.management.base import BaseCommand,CommandError
from sales.services.release_checks import release_report


class Command(BaseCommand):
    help='Read-only deployment gate: pinned runtime, schema, stock/ledger reconciliation and production static/security checks.'

    def add_arguments(self,parser):
        parser.add_argument('--production',action='store_true')
        parser.add_argument('--fail-on-blockers',action='store_true')

    def handle(self,*args,**options):
        report=release_report(production=options['production'])
        self.stdout.write(json.dumps(report,indent=2))
        if options['fail_on_blockers'] and not report['ready']:
            raise CommandError('IMS release checks did not pass. Review the reported blockers; no changes were made.')

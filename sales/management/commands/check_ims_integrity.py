"""Read-only financial and stock reconciliation, with an optional release gate."""
import json
from django.core.management.base import BaseCommand,CommandError
from sales.services.integrity import integrity_report


class Command(BaseCommand):
    help='Report stock, reservations, payments and posted-ledger anomalies without changing records.'

    def add_arguments(self,parser):
        parser.add_argument('--strict',action='store_true',help='Exit unsuccessfully when reconciliation findings exist.')

    def handle(self,*args,**options):
        report=integrity_report()
        self.stdout.write(json.dumps(report,indent=2,default=str))
        if options['strict'] and any(value for key,value in report.items() if key!='note'):
            raise CommandError('Reconciliation findings require review; no records were changed.')

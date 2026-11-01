"""Offline configuration check. No provider request, email or database query."""
from email.utils import parseaddr
from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.validators import validate_email
from django.core.exceptions import ValidationError


class Command(BaseCommand):
    help='Check report email configuration privately without contacting a provider or changing activation.'
    requires_system_checks=[]
    def handle(self,*args,**options):
        sender=False
        try:
            address=parseaddr(settings.DEFAULT_FROM_EMAIL)[1];validate_email(address)
            sender=address.rsplit('@',1)[-1].casefold() not in ('localhost','localhost.localdomain')
        except ValidationError:pass
        checks={
            'Improvement workflow enabled':getattr(settings,'TASKER_IMPROVEMENT_ENABLED',False),
            'Report pipeline enabled':getattr(settings,'TASKER_REPORTS_ENABLED',False),
            'Server email activation enabled':getattr(settings,'TASKER_REPORT_EMAIL_LIVE_ENABLED',False),
            'Email host configured':bool(settings.EMAIL_HOST),
            'Sender address valid':sender,
            'Email port in range':1<=settings.EMAIL_PORT<=65535,
            'TLS/SSL settings compatible':not(settings.EMAIL_USE_TLS and settings.EMAIL_USE_SSL),
        }
        for label,value in checks.items():self.stdout.write(f'{label}: {"yes" if value else "no"}')
        self.stdout.write('Offline check only. Company policy, recipients and provider authentication still require verification. No email sent; activation unchanged.')

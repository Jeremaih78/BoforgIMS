"""Guarded synthetic-only fixture for a separately created disposable database."""
from types import SimpleNamespace
from django.conf import settings
from django.core.management.base import BaseCommand,CommandError
from django.db import connection,transaction
from django.test import override_settings
from django.contrib.auth import get_user_model
from tasker.pilot_models import Pilot


class Command(BaseCommand):
    help='Create an invented pilot only on the designated loopback rehearsal database; no provider/send/deploy.'
    def handle(self,*args,**options):
        db=connection.settings_dict
        if connection.vendor!='postgresql' or db['HOST']!='127.0.0.1' or str(db['PORT'])!='55441' or db['NAME']!='ims_stage13_rehearsal':
            raise CommandError('Use only PostgreSQL ims_stage13_rehearsal on 127.0.0.1:55441.')
        if not settings.DEBUG or getattr(settings,'TASKER_ROLLOUT_WRITES_ENABLED',False) or getattr(settings,'TASKER_REPORT_EMAIL_LIVE_ENABLED',False) or getattr(settings,'TASKER_AI_OPENAI_API_KEY',''):
            raise CommandError('Rehearsal requires debug, no live rollout/send and an empty AI credential override.')
        if Pilot.all_objects.exists() or get_user_model().objects.filter(username__startswith='pilot-').exists():
            raise CommandError('Rehearsal fixtures already exist. Inspect this database; do not overwrite evidence.')
        with override_settings(TASKER_PILOT_ENABLED=True),transaction.atomic():
            from tasker.tests.pilot_fixtures import fixtures
            from tasker.tests.pilot_journey import run_journey
            data=SimpleNamespace();fixtures(data,synthetic=True)
            journey=run_journey(data)
            for user in (data.manager,data.worker,data.outsider):
                user.set_password('synthetic-preview-only');user.save(update_fields=['password'])
        self.stdout.write(f'Synthetic pilot {data.pilot.pk}; invented roster 8; accepted employee-days 320. Productivity validation remains pending.')
        self.stdout.write('Cross-stage journey: '+str(journey))
        self.stdout.write('Loopback preview logins: pilot-manager / pilot-operator-1 / pilot-restricted. Password: synthetic-preview-only')

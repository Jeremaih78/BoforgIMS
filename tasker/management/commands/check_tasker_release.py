"""Offline read-only release check; never authenticates providers or deploys."""
import json
from django.core.management.base import BaseCommand,CommandError
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied,ObjectDoesNotExist
from tasker.services.pilot_release import local_evidence,release_readiness,requirement_catalog
from tasker.services.pilot_metrics import pilot_report
from tasker.services.pilots import visible_pilots,manages


class Command(BaseCommand):
    help='Read source-bound local verification and optional authorized pilot gates without writes or provider calls.'
    def add_arguments(self,parser):
        parser.add_argument('--pilot',type=int)
        parser.add_argument('--user')
        parser.add_argument('--fail-on-blockers',action='store_true')
    def handle(self,*args,**options):
        local=local_evidence()
        result={'local_candidate':'VERIFIED' if local['current'] and all(v=='VERIFIED' for v in local['checks'].values()) else 'PENDING',
            'source_digest':local['source_digest'],'local_checks':local['checks'],'requirements':len(requirement_catalog()),
            'real_pilot':'PENDING','real_productivity':'PENDING','provider_request':False,'database_write':False}
        if options['pilot'] or options['user']:
            if not options['pilot'] or not options['user']:raise CommandError('Supply both --pilot and --user for a scoped report.')
            try:
                user=get_user_model().objects.get(username=options['user'],is_active=True)
                pilot=visible_pilots(user).get(pk=options['pilot'])
                if not manages(user,pilot):raise PermissionDenied
                release=release_readiness(user=user,pilot=pilot);report=pilot_report(user=user,pilot=pilot)
            except (PermissionDenied,ObjectDoesNotExist):raise CommandError('No authorized pilot in this scope.') from None
            result.update(real_pilot=release['deployment_gate'],real_productivity=report['productivity_gate'],
                synthetic=pilot.is_synthetic,productivity_blockers=report['blockers'],
                operating_checks={v['key']:v['status'] for v in release['operating']},health=release['health'])
        self.stdout.write(json.dumps(result,indent=2))
        if options['fail_on_blockers'] and (result['local_candidate']!='VERIFIED' or result['real_pilot']!='READY_FOR_SEPARATE_AUTHORIZATION'):
            raise CommandError('Release remains blocked by the reported evidence gates.')

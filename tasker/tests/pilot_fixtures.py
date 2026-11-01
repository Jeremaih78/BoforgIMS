"""Invented measurement fixtures. Only tests and the isolated rehearsal command use these."""
import uuid
from datetime import date,datetime,timedelta,timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.utils import timezone
from accounting.models import Company
from tasker.models import Team,TeamMembership,OrganizationAccess,BaselineStudy,BaselineObservation
from tasker.pilot_models import Pilot,PilotMember,PilotObservation,DEFINITIONS
from tasker.services import pilots

NOW=datetime(2026,10,31,12,tzinfo=dt_timezone.utc)
START=date(2026,9,7)
END=date(2026,10,2)


def fixtures(obj,*,synthetic=False,measurements=True):
    obj.manager=get_user_model().objects.create_user('pilot-manager',email='pilot@example.invalid')
    obj.outsider=get_user_model().objects.create_user('pilot-restricted')
    perms=list(Permission.objects.filter(content_type__app_label='tasker'))
    for u in (obj.manager,obj.outsider):u.user_permissions.add(*perms)
    obj.company=Company.objects.create(name='SYNTHETIC Stage 13 rehearsal')
    obj.team=Team.objects.create(company=obj.company,name='SYNTHETIC matched printing operators')
    TeamMembership.objects.create(team=obj.team,user=obj.manager,role='MANAGER')
    OrganizationAccess.objects.create(company=obj.company,user=obj.manager,can_manage=True,can_view_all=True)
    obj.workers=[]
    for n in range(8):
        u=get_user_model().objects.create_user(f'pilot-operator-{n+1}')
        u.user_permissions.add(*perms);TeamMembership.objects.create(team=obj.team,user=u);obj.workers.append(u)
    obj.worker=obj.workers[0]
    obj.study=BaselineStudy.objects.create(company=obj.company,team=obj.team,name='SYNTHETIC accepted print batches',
        role='Printing operator',workload_type='Standard small print batch',output_unit='batches',
        output_definition='Accepted source-register batches, counted once.',acceptance_criteria='Independent quality approval.',
        effort_definition='Rostered staffed hours including overtime, independent of timers.',quality_definition='Passes / checked batches and rework incidents.',
        source_definition='SYNTHETIC register and staffing sheets; invented evidence.',comparison_plan='Four matched pairs; later group remains unexposed.',
        start_date=date(2026,8,3),end_date=date(2026,8,28),status='COLLECTING',
        collection_started_at=datetime(2026,8,3,0,tzinfo=dt_timezone.utc))
    obj.pilot=pilots.save_pilot(user=obj.manager,study=obj.study,client_key=uuid.uuid4(),
        title='SYNTHETIC staged printing pilot',owner=obj.manager,is_synthetic=synthetic,method='STAGGERED',
        starts_on=START,ends_on=END,weekdays=[0,1,2,3,4],excluded_dates=[],features=['core','planning','coordination','business'],
        definitions={k:'SYNTHETIC predeclared source policy and independent review. No private reflections.' for k in DEFINITIONS})
    for n,u in enumerate(obj.workers):
        obj.pilot.refresh_from_db()
        pilots.add_member(user=obj.manager,pk=obj.pilot.pk,expected_revision=obj.pilot.revision,client_key=uuid.uuid4(),
            employee=u,arm='PILOT' if n%2==0 else 'COMPARISON',match_key=f'pair-{n//2+1}',
            rollout_on=START if n%2==0 else END+timedelta(days=3))
    obj.pilot.refresh_from_db()
    with patch('django.utils.timezone.now',return_value=datetime(2026,8,30,12,tzinfo=dt_timezone.utc)):
        obj.pilot=pilots.approve_protocol(user=obj.manager,pk=obj.pilot.pk,expected_revision=obj.pilot.revision,client_key=uuid.uuid4())
    if not synthetic:
        # Test-only simulated prior release; the rehearsal command always uses synthetic=True.
        Pilot.objects.filter(pk=obj.pilot.pk).update(rollout_authorized_at=datetime(2026,9,1,12,tzinfo=dt_timezone.utc),rollout_authorized_by=obj.manager)
        obj.pilot.refresh_from_db()
    if not measurements:return
    bases=[];supplements=[]
    for n,u in enumerate(obj.workers):
        for day in pilots._days(obj.study.start_date,obj.study.end_date,obj.pilot):
            bases.append(BaselineObservation(study=obj.study,employee=u,observed_on=day,role=obj.study.role,workload_type=obj.study.workload_type,
                staffed_hours=8,accepted_output=40,quality_passed=40,rework_count=0,evidence='SYNTHETIC baseline source sheet',
                source_key=f'base-{u.pk}-{day}',status='ACCEPTED',accepted_by=obj.manager,accepted_at=NOW))
    BaselineObservation.objects.bulk_create(bases)
    for base in bases:
        supplements.append(PilotObservation(pilot=obj.pilot,employee=base.employee,phase='BASELINE',baseline_observation=base,
            observed_on=base.observed_on,source_key=f'supplement-{base.pk}',quality_checked=40,cycle_minutes=320,cycle_samples=40,
            blocked_hours=0,deadline_due=40,deadline_met=40,overtime_hours=0,admin_hours=Decimal('.2'),tasker_exposed=False,
            status='ACCEPTED',accepted_at=NOW,accepted_by=obj.manager,evidence='SYNTHETIC baseline timing/quality supplement'))
    for n,u in enumerate(obj.workers):
        output=76+(n//2)*4 if n%2==0 else 40
        for day in pilots._days(START,END,obj.pilot):
            supplements.append(PilotObservation(pilot=obj.pilot,employee=u,phase='FOLLOWUP',observed_on=day,source_key=f'follow-{u.pk}-{day}',
                staffed_hours=8,accepted_output=output,quality_passed=output,quality_checked=output,rework_count=0,
                cycle_minutes=320,cycle_samples=output,blocked_hours=0,deadline_due=output,deadline_met=output,overtime_hours=0,
                admin_hours=Decimal('.2'),tasker_exposed=n%2==0,status='ACCEPTED',accepted_at=NOW,accepted_by=obj.manager,
                evidence='SYNTHETIC independently accepted register and staffed-hours sheet'))
    PilotObservation.objects.bulk_create(supplements)

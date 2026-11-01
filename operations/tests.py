"""Cross-source lifecycle, scope, stale drafts and source-authoritative output acceptance."""
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone
from django.urls import reverse
from django_hosts.resolvers import reverse as host_reverse

from accounting.models import Company, Currency, FiscalPeriod, JournalEntry, SupplierBill
from customers.models import Customer
from inventory.models import Product, ProductUnit, ServiceCase, Shipment, StockMovement, Stocktake
from inventory.models import Supplier
from sales.models import Quotation
from shop.models import Order, Payment
from tasker.business_models import BusinessLinkEvent, BusinessRecordLink
from tasker.business_outcome_models import BusinessOutcomeEvidence
from tasker.goal_models import Goal, KeyResult, OutcomeMeasurement
from tasker.models import OrganizationAccess, Task, Team, TeamMembership
from tasker.services import business_links
from tasker.services.business_guidance import board_business_work, next_business_move
from tasker.services.business_outcomes import accept_source_output, attribute
from tasker.services.goals import accept_outcome, key_result_progress
from .models import Responsibility, ResponsibilityEvent
from .registry import CHECKLISTS, SOURCES, facts
from .services import configure, get_row, review


FLAGS={f'TASKER_{kind.removesuffix("_work").upper()}_ADAPTER_ENABLED':True for kind in SOURCES}


@override_settings(**FLAGS)
class OperationalWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager=get_user_model().objects.create_user('operations-manager')
        cls.worker=get_user_model().objects.create_user('operations-worker')
        cls.peer=get_user_model().objects.create_user('operations-peer')
        cls.outsider=get_user_model().objects.create_user('operations-outsider')
        permissions=Permission.objects.filter(content_type__app_label__in=(
            'tasker','operations','inventory','sales','shop','accounting'))
        for user in (cls.manager,cls.worker,cls.peer,cls.outsider):
            user.user_permissions.add(*permissions)
        cls.company=Company.objects.create(name='Synthetic operations company')
        cls.other_company=Company.objects.create(name='Other operations company')
        cls.team=Team.objects.create(company=cls.company,name='Operational execution')
        for user in (cls.manager,cls.worker,cls.peer):
            TeamMembership.objects.create(team=cls.team,user=user,
                role='MANAGER' if user==cls.manager else 'EMPLOYEE')
        OrganizationAccess.objects.create(user=cls.manager,company=cls.company,can_manage=True,can_view_all=True)
        OrganizationAccess.objects.create(user=cls.outsider,company=cls.other_company,can_manage=True)
        cls.product=Product.objects.create(name='Synthetic service product',sku='OPS-SYNTH',quantity=5,reserved=0)
        cls.unit=ProductUnit.objects.create(product=cls.product,status='FAULTY')
        cls.supplier=Supplier.objects.create(name='Synthetic supplier')
        cls.customer=Customer.objects.create(name='PRIVATE_CUSTOMER_NOT_PROJECTED')
        cls.currency,_=Currency.objects.get_or_create(code='USD',defaults={'name':'US Dollar'})
        cls.goal=Goal.objects.create(company=cls.company,team=cls.team,title='Accepted operational outputs',
            status='ACTIVE',evidence_policy='Verify completed authoritative source and accept one operational output.')
        cls.result=KeyResult.objects.create(goal=cls.goal,title='Operational outputs',unit='outputs',baseline=0,target=100)

    def source(self,kind):
        if kind=='shipment_work':
            return Shipment.objects.create(supplier=self.supplier,origin_country='ZW',destination_country='ZW',
                incoterm='FOB',shipping_method='ROAD',eta_date=timezone.localdate())
        if kind=='service_work':
            return ServiceCase.objects.create(unit=self.unit,problem='PRIVATE_REPAIR_BODY')
        if kind=='stocktake_work':
            return Stocktake.objects.create(name='Synthetic review',status='REVIEW',created_by=self.manager)
        if kind=='quotation_work':
            return Quotation.objects.create(customer=self.customer,status='SENT',notes='PRIVATE_QUOTE_BODY')
        if kind=='fulfilment_work':
            order=Order.objects.create(number='OPS-'+uuid.uuid4().hex[:12],email='private@example.invalid',total=10,status='paid')
            Payment.objects.create(order=order,status='paid')
            return order
        if kind=='bill_work':
            return SupplierBill.objects.create(company=self.company,supplier=self.supplier,currency=self.currency,
                doc_no='BILL-'+uuid.uuid4().hex[:10])
        return FiscalPeriod.objects.create(company=self.company,name='OPS-'+uuid.uuid4().hex[:8],
            start_date=timezone.localdate()-timedelta(days=30),end_date=timezone.localdate())

    def configured(self,kind,source=None,**changes):
        source=source or self.source(kind)
        values=dict(user=self.manager,kind=kind,source_id=source.pk,company_id=self.company.pk,
            team_id=self.team.pk,assigned_to_id=self.worker.pk,enabled=True,next_review_on=timezone.localdate(),
            contact_interval_days=3 if kind=='quotation_work' else None,
            sla_due_on=timezone.localdate() if kind=='service_work' else None,
            expected_revision='new',client_key=uuid.uuid4())
        values.update(changes)
        return configure(**values)

    def sync(self,row):
        return business_links.reconcile_business_source(source_type=row.kind,source_id=row.pk)

    def update(self,row,**changes):
        row=get_row(self.worker,row.pk)
        values=dict(user=self.worker,pk=row.pk,expected_revision=row.revision,
            expected_source_version=row.current['version'],client_key=uuid.uuid4(),
            next_review_on=timezone.localdate()+timedelta(days=2),notes='PRIVATE_OPERATIONAL_NOTES',
            checklist={key:False for key in CHECKLISTS.get(row.kind,())})
        values.update(changes)
        return review(**values)

    def close(self,row):
        spec=SOURCES[row.kind]
        changes={'status':spec.done[0]} if spec.done and spec.status_field=='status' else {}
        if row.kind=='stocktake_work':changes.update(approved_by_id=self.manager.pk,closed_at=timezone.now())
        if row.kind=='period_work':changes={'is_closed':True,'locked_at':timezone.now()}
        if row.kind=='service_work':
            changes={'status':'CLOSED','inspection':'Inspection completed','resolution':'Resolution verified',
                'closed_at':timezone.now(),'technician_id':self.worker.pk}
        spec.cls().objects.filter(pk=row.source_id).update(**changes)

    def test_all_seven_sources_replay_one_action_without_task_or_finance_writes(self):
        tasks=Task.objects.count();movements=StockMovement.objects.count();journals=JournalEntry.objects.count()
        for kind in SOURCES:
            with self.subTest(kind=kind):
                row=self.configured(kind)
                link=self.sync(row)
                replay=self.sync(row)
                self.assertEqual(link.pk,replay.pk)
                self.assertEqual(link.revision,replay.revision)
                self.assertEqual(link.events.count(),1)
                shown=business_links.get_business_link(user=self.worker,pk=link.pk)
                self.assertEqual(shown.status,'OPEN')
                self.assertFalse(shown.source_stale)
                self.assertIn(link.pk,business_links.visible_business_links_for(self.worker,state='active',mine=True).values_list('pk',flat=True))
                self.assertEqual(business_links.business_notification_links_for(self.worker).filter(pk=link.pk).count(),1)
                data=str(link.__dict__)
                for secret in ('PRIVATE_REPAIR_BODY','PRIVATE_QUOTE_BODY','PRIVATE_CUSTOMER','private@example'):
                    self.assertNotIn(secret,data)
        self.assertEqual(Task.objects.count(),tasks)
        self.assertEqual(StockMovement.objects.count(),movements)
        self.assertEqual(JournalEntry.objects.count(),journals)
        self.assertEqual(OutcomeMeasurement.objects.count(),0)

    def test_all_seven_bulk_source_changes_hide_stale_notifications_and_complete_from_source(self):
        for kind in SOURCES:
            with self.subTest(kind=kind):
                row=self.configured(kind);link=self.sync(row)
                self.close(row)
                self.assertFalse(business_links.business_notification_available(user=self.worker,link=link,source_revision=link.revision))
                refreshed=self.sync(row)
                self.assertEqual(refreshed.status,'IN_PROGRESS' if kind=='service_work' else 'COMPLETED')
                self.assertEqual(self.sync(row).revision,refreshed.revision)

    def test_every_source_requires_native_capability_and_company_scope(self):
        from .policies import visible
        tasker_only=get_user_model().objects.create_user('ops-tasker-only')
        tasker_only.user_permissions.add(*Permission.objects.filter(content_type__app_label='tasker'))
        TeamMembership.objects.create(user=tasker_only,team=self.team)
        for kind in SOURCES:
            row=self.configured(kind);link=self.sync(row)
            self.assertFalse(visible(self.outsider).filter(pk=row.pk).exists())
            self.assertFalse(visible(tasker_only).filter(pk=row.pk).exists())
            with self.assertRaises(PermissionDenied):business_links.get_business_link(user=self.outsider,pk=link.pk)

    def test_manager_cannot_assign_foreign_employee_or_team(self):
        with self.assertRaises(ValidationError):self.configured('shipment_work',assigned_to_id=self.outsider.pk)
        foreign=Team.objects.create(company=self.other_company,name='Foreign team')
        with self.assertRaises(ValidationError):self.configured('shipment_work',team_id=foreign.pk)

    def test_quotation_requires_owner_date_and_interval_and_uses_policy_after_contact(self):
        source=self.source('quotation_work')
        for change in ({'assigned_to_id':None},{'next_review_on':None},{'contact_interval_days':None}):
            with self.assertRaises(ValidationError):self.configured('quotation_work',source=source,**change)
        row=self.configured('quotation_work',source=source)
        row=self.update(row,contact_done=True)
        self.assertEqual(row.next_review_on,timezone.localdate()+timedelta(days=3))
        source.refresh_from_db();self.assertEqual(source.status,'SENT')

    def test_repair_sla_and_manager_quality_acceptance_require_source_evidence(self):
        with self.assertRaises(ValidationError):self.configured('service_work',sla_due_on=None)
        row=self.configured('service_work')
        with self.assertRaises(PermissionDenied):self.update(row,accept_quality=True)
        with self.assertRaises(ValidationError):self.update(row,user=self.manager,accept_quality=True)
        self.close(row)
        row=self.update(row,user=self.manager,accept_quality=True,
            checklist={key:True for key in CHECKLISTS['service_work']})
        self.assertEqual(facts(row)['status'],'COMPLETED')
        ServiceCase.objects.filter(pk=row.source_id).update(resolution='Changed resolution')
        self.assertEqual(facts(Responsibility.objects.get(pk=row.pk))['status'],'IN_PROGRESS')

    def test_checklists_cannot_close_period_approve_stocktake_or_move_stock(self):
        for kind in ('period_work','stocktake_work'):
            row=self.configured(kind)
            self.update(row,checklist={key:True for key in CHECKLISTS.get(kind,())})
            source=SOURCES[kind].cls().objects.get(pk=row.source_id)
            self.assertFalse(getattr(source,'is_closed',False))
            if kind=='stocktake_work':self.assertEqual(source.status,'REVIEW')
        self.assertFalse(StockMovement.objects.exists())

    def test_current_payment_and_finance_company_required(self):
        row=self.configured('fulfilment_work');link=self.sync(row)
        Payment.objects.filter(order_id=row.source_id).update(status='failed')
        self.assertFalse(business_links.visible_business_links_for(self.worker,state='active').filter(pk=link.pk).exists())
        self.assertEqual(self.sync(row).status,'UNAVAILABLE')
        bill=self.source('bill_work')
        SupplierBill.objects.filter(pk=bill.pk).update(company=self.other_company)
        with self.assertRaises(ValidationError):self.configured('bill_work',source=bill)

    def test_source_change_rejects_stale_review_and_retains_draft_in_http(self):
        row=self.configured('shipment_work');current=get_row(self.worker,row.pk)
        Shipment.objects.filter(pk=row.source_id).update(status='ARRIVED')
        with self.assertRaises(ValidationError):self.update(row,expected_source_version=current.current['version'])
        self.client.force_login(self.worker)
        response=self.client.post(reverse('ims:operations:detail',kwargs={'pk':row.pk}),
            dict(expected_revision=current.revision,expected_source_version=current.current['version'],client_key=uuid.uuid4(),
                next_review_on='',notes='Retain this unsaved draft'))
        self.assertEqual(response.status_code,409)
        self.assertContains(response,'Retain this unsaved draft',status_code=409)
        row.refresh_from_db();self.assertEqual(row.notes,'')

    def test_source_removal_retires_link_without_duplicate_or_authority(self):
        row=self.configured('quotation_work');link=self.sync(row)
        Quotation.objects.get(pk=row.source_id).delete()
        self.assertEqual(self.sync(row).status,'UNAVAILABLE')
        self.assertEqual(BusinessRecordLink.objects.filter(pk=link.pk).count(),1)

    def test_pause_and_independent_flags_retire_and_restore_existing_reference(self):
        rows=[self.configured(kind) for kind in ('shipment_work','stocktake_work')]
        links=[self.sync(row) for row in rows]
        with override_settings(TASKER_SHIPMENT_ADAPTER_ENABLED=False):
            self.assertEqual(self.sync(rows[0]).reason_code,'adapter_disabled')
            self.assertEqual(self.sync(rows[1]).status,'OPEN')
        self.assertEqual(self.sync(rows[0]).pk,links[0].pk)

    def test_review_request_replay_is_once_and_altered_replay_rejected(self):
        row=self.configured('shipment_work');key=uuid.uuid4()
        self.update(row,client_key=key)
        self.update(row,client_key=key)
        self.assertEqual(ResponsibilityEvent.objects.filter(client_key=key).count(),1)
        receipt=ResponsibilityEvent.objects.get(client_key=key)
        self.assertEqual(receipt.details['owner_id'],self.worker.pk)
        self.assertNotIn('PRIVATE_OPERATIONAL_NOTES',str(receipt.details))
        with self.assertRaises(ValidationError):self.update(row,client_key=key,notes='Different work')

    def test_source_output_acceptance_counts_once_and_reopen_invalidates_progress_immediately(self):
        row=self.configured('stocktake_work');self.close(row);link=self.sync(row)
        attribute(user=self.manager,link_id=link.pk,team=self.team,key_result=self.result,expected_revision=link.revision)
        first=accept_source_output(user=self.manager,link_id=link.pk,expected_revision=link.revision)
        second=accept_source_output(user=self.manager,link_id=link.pk,expected_revision=link.revision)
        self.assertEqual(first.pk,second.pk)
        self.assertEqual(key_result_progress(self.result)['accepted_count'],1)
        Stocktake.objects.filter(pk=row.source_id).update(status='REVIEW')
        self.assertEqual(key_result_progress(self.result)['accepted_count'],0)
        with self.assertRaises(ValidationError):accept_outcome(outcome=first,user=self.manager)
        self.sync(row);first.refresh_from_db();self.assertEqual(first.status,'RETRACTED')
        self.assertEqual(BusinessOutcomeEvidence.objects.count(),1)

    def test_output_mapping_cannot_claim_money_or_credit_live_work(self):
        row=self.configured('bill_work');link=self.sync(row)
        attribute(user=self.manager,link_id=link.pk,team=self.team,key_result=self.result,expected_revision=link.revision)
        with self.assertRaises(ValidationError):accept_source_output(user=self.manager,link_id=link.pk,expected_revision=link.revision)
        self.close(row);link=self.sync(row)
        KeyResult.objects.filter(pk=self.result.pk).update(unit='USD')
        with self.assertRaises(ValidationError):accept_source_output(user=self.manager,link_id=link.pk,expected_revision=link.revision)
        self.assertFalse(OutcomeMeasurement.objects.exists())

    def test_board_uses_explicit_team_and_does_not_duplicate_people_memberships(self):
        row=self.configured('shipment_work');link=self.sync(row)
        otherteam=Team.objects.create(company=self.company,name='Second membership')
        TeamMembership.objects.create(user=self.worker,team=otherteam)
        data=board_business_work(self.manager,pairs={(self.worker.pk,self.team.pk),(self.worker.pk,otherteam.pk)})
        self.assertEqual(len(data['lanes'][(self.worker.pk,self.team.pk)]),1)
        self.assertNotIn((self.worker.pk,otherteam.pk),data['lanes'])
        Responsibility.objects.filter(pk=row.pk).update(team=None)
        data=board_business_work(self.manager,pairs={(self.worker.pk,self.team.pk)})
        self.assertEqual(len(data['unallocated']),1)

    def test_operator_integrates_business_priority_and_keeps_active_focus(self):
        row=self.configured('fulfilment_work');self.sync(row)
        task=Task.objects.create(owner=self.worker,title='Routine task',priority='LOW')
        self.assertIsNotNone(next_business_move(self.worker,selected_task=task))
        task.priority='CRITICAL'
        self.assertIsNone(next_business_move(self.worker,selected_task=task))
        self.assertIsNone(next_business_move(self.worker,focus_active=True))

    def test_ui_read_only_get_source_links_configuration_and_csrf(self):
        row=self.configured('shipment_work');link=self.sync(row)
        self.client.force_login(self.manager)
        for route,kwargs in (('home',{}),('detail',{'pk':row.pk}),('configure_existing',{'pk':row.pk})):
            response=self.client.get(reverse('ims:operations:'+route,kwargs=kwargs))
            self.assertEqual(response.status_code,200)
        response=self.client.get(reverse('ims:tasker:business_detail',kwargs={'pk':link.pk}))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Goal and team attribution')
        self.assertEqual(ResponsibilityEvent.objects.count(),1)

    def test_worker_failure_rolls_back_projection_and_replay_repairs_once(self):
        row=self.configured('shipment_work')
        with patch('tasker.services.notifications.queue_business_link_update',side_effect=RuntimeError('Synthetic worker fault')):
            with self.assertRaises(RuntimeError):self.sync(row)
        self.assertFalse(BusinessRecordLink.objects.exists())
        link=self.sync(row);self.assertEqual(self.sync(row).pk,link.pk)
        self.assertEqual(BusinessLinkEvent.objects.count(),1)

    def test_bounded_repair_rotates_across_seven_independent_sources(self):
        rows=[self.configured(kind) for kind in SOURCES]
        for _ in range(35):
            result=business_links.reconcile_business_links(limit=1)
            self.assertLessEqual(result['scanned'],1)
        from tasker.job_models import OutboxJob
        kinds={job.payload['source_type'] for job in OutboxJob.objects.filter(kind='business.reconcile')}
        self.assertTrue(set(SOURCES).issubset(kinds))

    def test_all_sources_cancel_stale_reminders_after_reassignment_and_permission_revocation(self):
        from tasker.notification_models import Notification, NotificationDelivery, NotificationPreference
        from tasker.services.notifications import deliver_notification
        for user in (self.worker,self.peer):
            NotificationPreference.objects.create(owner=user,respect_work_hours=False,suppress_during_focus=False)
        for kind in SOURCES:
            with self.subTest(kind=kind):
                row=self.configured(kind);link=self.sync(row)
                notice=Notification.objects.get(business_link=link,cancelled_at__isnull=True)
                delivery=NotificationDelivery.objects.get(notification=notice)
                Responsibility.objects.filter(pk=row.pk).update(assigned_to=self.peer)
                self.assertFalse(business_links.business_notification_available(user=self.worker,link=link,source_revision=link.revision))
                self.assertTrue(deliver_notification({'delivery_id':delivery.pk}).get('cancelled'))
                self.sync(row)
                current=Notification.objects.get(business_link=link,cancelled_at__isnull=True)
                self.assertEqual(current.owner_id,self.peer.pk)
                delivery=NotificationDelivery.objects.get(notification=current)
                self.assertEqual(delivery.channel,'IN_APP')
                self.assertEqual(deliver_notification({'delivery_id':delivery.pk}).get('delivered'),1)
                # No team-wide or manager CC audience is created.
                self.assertEqual(Notification.objects.filter(business_link=link,owner=self.manager).count(),0)

    def test_deleted_source_and_paused_adapter_never_hydrate_disabled_native_tables(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        row=self.configured('shipment_work');link=self.sync(row)
        with override_settings(TASKER_SHIPMENT_ADAPTER_ENABLED=False),CaptureQueriesContext(connection) as queries:
            self.sync(row)
        self.assertFalse(any('inventory_shipment' in q['sql'].lower() for q in queries.captured_queries))

    def test_late_worker_lease_loss_rolls_back_operational_action_and_attention(self):
        from tasker.services.jobs import LostJobLease
        row=self.configured('shipment_work')
        with patch('tasker.services.jobs.assert_current_job_lease'),patch(
                'tasker.services.jobs.fence_current_job_effects',side_effect=LostJobLease):
            with self.assertRaises(LostJobLease):
                business_links.handle_business_source(payload={'source_type':row.kind,'source_id':row.pk})
        self.assertFalse(BusinessRecordLink.objects.exists())
        self.assertEqual(self.sync(row).status,'OPEN')

    def test_missed_source_hooks_are_repaired_with_reference_only_jobs(self):
        from tasker.job_models import OutboxJob
        row=self.configured('shipment_work');link=self.sync(row)
        OutboxJob.objects.all().delete()
        Shipment.objects.filter(pk=row.source_id).update(status='ARRIVED')
        for _ in range(3):business_links._reconcile_operations(kind=row.kind,limit=10)
        jobs=list(OutboxJob.objects.filter(kind='business.reconcile'))
        self.assertEqual(len(jobs),1)
        self.assertEqual(jobs[0].payload,{'source_type':row.kind,'source_id':row.pk})
        self.assertEqual(self.sync(row).source_status,'ARRIVED')

    def test_native_save_hook_is_committed_and_idempotent(self):
        from tasker.job_models import OutboxJob
        row=self.configured('shipment_work');self.sync(row)
        OutboxJob.objects.all().delete()
        source=Shipment.objects.get(pk=row.source_id)
        source.status='ARRIVED'
        with self.captureOnCommitCallbacks(execute=True):source.save(update_fields=['status','updated_at'])
        self.assertEqual(OutboxJob.objects.filter(kind='business.reconcile').count(),1)

    def test_unauthorized_manager_and_csrf_cannot_write_responsibility(self):
        from django.test import Client
        row=self.configured('shipment_work')
        with self.assertRaises(PermissionDenied):
            self.update(row,user=self.outsider)
        client=Client(enforce_csrf_checks=True);client.force_login(self.manager)
        self.assertEqual(client.post(reverse('ims:operations:detail',kwargs={'pk':row.pk}),{}).status_code,403)

    def test_all_sources_lose_action_access_when_employee_native_grant_is_revoked(self):
        rows=[self.configured(kind) for kind in SOURCES]
        links=[self.sync(row) for row in rows]
        self.worker.user_permissions.remove(*Permission.objects.filter(content_type__app_label__in=(
            'inventory','sales','shop','accounting')))
        for link in links:
            self.assertFalse(business_links.visible_business_links_for(self.worker).filter(pk=link.pk).exists())
            self.assertFalse(business_links.business_notification_available(user=self.worker,link=link,source_revision=link.revision))

    def test_unassigned_operational_action_remains_visible_to_source_manager(self):
        row=self.configured('shipment_work',assigned_to_id=None)
        link=self.sync(row)
        self.assertEqual(link.status,'UNASSIGNED')
        self.assertTrue(business_links.visible_business_links_for(self.manager,state='active').filter(pk=link.pk).exists())
        self.assertFalse(business_links.business_actions_for(self.worker)['items'])

    def test_current_source_priority_drives_ranking_and_stale_priority_notice_is_hidden(self):
        row=self.configured('bill_work',priority='CRITICAL');link=self.sync(row)
        self.assertEqual(link.priority,'CRITICAL')
        task=Task.objects.create(owner=self.worker,title='High task',priority='HIGH')
        self.assertEqual(next_business_move(self.worker,selected_task=task)['item'].pk,link.pk)
        Responsibility.objects.filter(pk=row.pk).update(priority='LOW')
        self.assertFalse(business_links.business_notification_available(user=self.worker,link=link,source_revision=link.revision))
        self.assertIsNone(next_business_move(self.worker,selected_task=task))

    def test_source_ui_configuration_submission_uses_explicit_company_and_owner(self):
        self.client.force_login(self.manager)
        source=self.source('shipment_work')
        url=reverse('ims:operations:configure')+f'?kind=shipment_work&company={self.company.pk}'
        response=self.client.post(url,dict(source=source.pk,team=self.team.pk,assigned_to=self.worker.pk,
            enabled='on',priority='CRITICAL',next_review_on=timezone.localdate(),expected_revision='new',client_key=uuid.uuid4()))
        self.assertEqual(response.status_code,302)
        row=Responsibility.objects.get(kind='shipment_work',source_id=source.pk)
        self.assertEqual(row.priority,'CRITICAL')
        self.assertEqual(row.assigned_to_id,self.worker.pk)

    def test_source_dates_and_current_identifier_match_notification_sql(self):
        row=self.configured('service_work',next_review_on=timezone.localdate()+timedelta(days=5))
        link=self.sync(row)
        self.assertEqual(link.due_date,timezone.localdate())
        self.assertIn(self.unit.unit_id,link.title)
        self.assertTrue(business_links.business_notification_links_for(self.worker).filter(pk=link.pk).exists())

    def test_one_source_action_cannot_be_credited_again_after_reassignment(self):
        row=self.configured('stocktake_work');self.close(row);link=self.sync(row)
        attribute(user=self.manager,link_id=link.pk,team=self.team,key_result=self.result,expected_revision=link.revision)
        accept_source_output(user=self.manager,link_id=link.pk,expected_revision=link.revision)
        Responsibility.objects.filter(pk=row.pk).update(assigned_to=self.peer)
        link=self.sync(row)
        with self.assertRaises(ValidationError):
            accept_source_output(user=self.manager,link_id=link.pk,expected_revision=link.revision)
        self.assertEqual(OutcomeMeasurement.objects.count(),1)
        self.assertEqual(key_result_progress(self.result)['accepted_count'],0)

    def test_team_manager_can_delegate_own_team_without_company_wide_access(self):
        OrganizationAccess.objects.filter(user=self.manager).delete()
        row=self.configured('shipment_work')
        self.assertEqual(row.team_id,self.team.pk)
        otherteam=Team.objects.create(company=self.company,name='Another manager team')
        TeamMembership.objects.create(user=self.peer,team=otherteam)
        with self.assertRaises(PermissionDenied):
            self.configured('shipment_work',team_id=otherteam.pk,assigned_to_id=self.peer.pk)
        self.client.force_login(self.manager)
        response=self.client.get(reverse('ims:operations:configure')+f'?kind=shipment_work&company={self.company.pk}')
        self.assertEqual(response.status_code,200)
        self.assertNotContains(response,'Another manager team')

    def test_posted_and_partially_paid_bills_remain_in_due_review_until_paid_at_source(self):
        row=self.configured('bill_work')
        for state in ('POSTED','PARTIAL'):
            SupplierBill.objects.filter(pk=row.source_id).update(status=state)
            self.assertEqual(self.sync(row).status,'OPEN')
        SupplierBill.objects.filter(pk=row.source_id).update(status='PAID')
        self.assertEqual(self.sync(row).status,'COMPLETED')

    def test_future_source_review_is_visible_in_work_but_does_not_displace_todays_next_move(self):
        row=self.configured('quotation_work',next_review_on=timezone.localdate()+timedelta(days=10))
        self.sync(row)
        self.assertIsNone(next_business_move(self.worker))
        self.assertEqual(len(business_links.business_actions_for(self.worker)['items']),1)

    def test_future_high_priority_work_cannot_starve_due_work_outside_display_limit(self):
        for _ in range(9):
            self.sync(self.configured('quotation_work',next_review_on=timezone.localdate()+timedelta(days=10)))
        due=self.configured('bill_work',priority='LOW');link=self.sync(due)
        self.assertEqual(next_business_move(self.worker)['item'].pk,link.pk)

    def test_due_repair_scan_emits_once_daily_without_repeating_todays_source_notice(self):
        from tasker.notification_models import Notification
        row=self.configured('shipment_work');link=self.sync(row)
        original=Notification.objects.get(business_link=link)
        self.assertIsNone(business_links.enqueue_business_source(row.kind,row.pk))
        self.assertEqual(Notification.objects.filter(business_link=link).count(),1)
        Notification.objects.filter(pk=original.pk).update(created_at=timezone.now()-timedelta(days=1))
        for _ in range(3):business_links.enqueue_business_source(row.kind,row.pk)
        self.assertEqual(Notification.objects.filter(business_link=link).count(),2)
        self.assertEqual(Notification.objects.filter(business_link=link,cancelled_at__isnull=True).count(),1)
        original.refresh_from_db();self.assertIsNotNone(original.cancelled_at)

    def test_native_bill_and_order_status_timestamps_do_not_invent_legacy_completion(self):
        from tasker.services.business_outcomes import completion_day
        for kind in ('bill_work','fulfilment_work'):
            row=self.configured(kind);native=SOURCES[kind].cls().objects.get(pk=row.source_id)
            self.assertIsNone(native.status_changed_at)
            native.status='PAID' if kind=='bill_work' else 'shipped'
            native.save(update_fields=['status'])
            stamp=native.status_changed_at
            self.assertIsNotNone(stamp)
            native.save(update_fields=['status'])
            self.assertEqual(native.status_changed_at,stamp)
            link=self.sync(row)
            self.assertEqual(completion_day(link),timezone.localdate(stamp))
            attribute(user=self.manager,link_id=link.pk,team=self.team,key_result=self.result,expected_revision=link.revision)
            output=accept_source_output(user=self.manager,link_id=link.pk,expected_revision=link.revision)
            self.assertEqual(output.occurred_on,timezone.localdate(stamp))

    def test_undated_legacy_completion_does_not_gain_credit_from_observation_time(self):
        row=self.configured('stocktake_work')
        Stocktake.objects.filter(pk=row.source_id).update(status='CLOSED',approved_by=self.manager,closed_at=None)
        link=self.sync(row)
        attribute(user=self.manager,link_id=link.pk,team=self.team,key_result=self.result,expected_revision=link.revision)
        with self.assertRaises(ValidationError):
            accept_source_output(user=self.manager,link_id=link.pk,expected_revision=link.revision)
        self.assertFalse(OutcomeMeasurement.objects.exists())

    def test_bulk_terminal_status_cannot_reuse_an_intermediate_status_date(self):
        from tasker.services.business_outcomes import completion_day
        row=self.configured('bill_work');bill=SupplierBill.objects.get(pk=row.source_id)
        bill.status='POSTED';bill.save(update_fields=['status'])
        self.assertIsNone(bill.status_changed_at)
        SupplierBill.objects.filter(pk=bill.pk).update(status='PAID')
        self.assertIsNone(completion_day(self.sync(row)))

    def test_source_views_expose_native_evidence_only_inside_authorized_source_scope(self):
        self.client.force_login(self.worker)
        for kind in ('bill_work','fulfilment_work','period_work'):
            row=self.configured(kind)
            response=self.client.get(reverse('ims:operations:detail',kwargs={'pk':row.pk}))
            self.assertEqual(response.status_code,200)
            self.assertContains(response,'at source')
            self.client.force_login(self.outsider)
            self.assertEqual(self.client.get(reverse('ims:operations:detail',kwargs={'pk':row.pk})).status_code,403)
            self.client.force_login(self.worker)

    @override_settings(TASKER_STRATEGY_ENABLED=True)
    def test_integrated_board_keeps_its_json_safe_contract_with_live_source_actions(self):
        import json
        from tasker.services.strategy import build_strategy_board
        row=self.configured('shipment_work',priority='CRITICAL');link=self.sync(row)
        attribute(user=self.manager,link_id=link.pk,team=self.team,key_result=self.result,expected_revision=link.revision)
        board=build_strategy_board(user=self.manager,team_id=self.team.pk)
        self.assertIn(str(link.pk),json.dumps(board))
        lane=next(item for item in board['lanes'] if item['user_id']==self.worker.pk)
        self.assertEqual(lane['business_actions'][0]['id'],str(link.pk))
        self.assertEqual(lane['summary']['source_count'],1)
        self.assertEqual(lane['summary']['source_critical'],1)
        goal=next(item for item in board['goals'] if item['id']==self.goal.pk)
        self.assertEqual(goal['linked_business_count'],1)
        self.assertFalse(any(item['code']=='orphan_goal' and item['detail'].startswith(self.goal.title) for item in board['exceptions']))
        self.client.force_login(self.manager)
        response=self.client.get(reverse('ims:tasker:strategy_board')+f'?team={self.team.pk}')
        self.assertEqual(response.status_code,200)
        self.assertContains(response,link.title)


from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.db import close_old_connections, connections
from django.test import TransactionTestCase


@override_settings(TASKER_SHIPMENT_ADAPTER_ENABLED=True)
class OperationalConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.manager=get_user_model().objects.create_user('ops-race-manager')
        self.worker=get_user_model().objects.create_user('ops-race-worker')
        for user in (self.manager,self.worker):
            user.user_permissions.add(*Permission.objects.filter(content_type__app_label__in=('tasker','operations','inventory')))
        self.company=Company.objects.create(name='Operational concurrency company')
        self.team=Team.objects.create(company=self.company,name='Operational concurrency team')
        TeamMembership.objects.create(team=self.team,user=self.worker)
        OrganizationAccess.objects.create(user=self.manager,company=self.company,can_manage=True,can_view_all=True)
        supplier=Supplier.objects.create(name='Operational concurrency supplier')
        self.source=Shipment.objects.create(supplier=supplier,origin_country='ZW',destination_country='ZW',incoterm='FOB',shipping_method='ROAD')

    def parallel(self,function):
        barrier=Barrier(2)
        def work(index):
            close_old_connections()
            try:
                barrier.wait(timeout=15)
                return function(index)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(work,i) for i in range(2)]
            return [future.result(timeout=40) for future in futures]

    def test_simultaneous_configuration_has_one_responsibility_and_a_stale_conflict(self):
        def work(index):
            try:
                row=configure(user=self.manager,kind='shipment_work',source_id=self.source.pk,company_id=self.company.pk,
                    team_id=self.team.pk,assigned_to_id=self.worker.pk,enabled=True,next_review_on=timezone.localdate(),
                    contact_interval_days=None,sla_due_on=None,expected_revision='new',client_key=uuid.uuid4())
                return 'saved'
            except ValidationError:
                return 'conflict'
        self.assertCountEqual(self.parallel(work),['saved','conflict'])
        self.assertEqual(Responsibility.objects.count(),1)

    def test_simultaneous_first_reconciliation_creates_one_action_and_transition(self):
        row=configure(user=self.manager,kind='shipment_work',source_id=self.source.pk,company_id=self.company.pk,
            team_id=self.team.pk,assigned_to_id=self.worker.pk,enabled=True,next_review_on=timezone.localdate(),
            contact_interval_days=None,sla_due_on=None,expected_revision='new',client_key=uuid.uuid4())
        results=self.parallel(lambda index:str(business_links.reconcile_business_source(source_type=row.kind,source_id=row.pk).pk))
        self.assertEqual(results[0],results[1])
        self.assertEqual(BusinessRecordLink.objects.count(),1)
        self.assertEqual(BusinessLinkEvent.objects.count(),1)

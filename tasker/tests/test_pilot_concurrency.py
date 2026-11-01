import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.db import close_old_connections,connection,connections
from django.test import TransactionTestCase,override_settings
from django.core.exceptions import ValidationError
from tasker.pilot_models import PilotEvent,PilotObservation
from tasker.services import pilots
from .pilot_fixtures import fixtures,START


@override_settings(TASKER_PILOT_ENABLED=True)
class PilotConcurrencyTests(TransactionTestCase):
    def setUp(self):fixtures(self,measurements=False)
    def parallel(self,fn):
        self.assertEqual(connection.vendor,'postgresql');barrier=Barrier(2)
        def worker():
            close_old_connections()
            try:barrier.wait(timeout=10);return fn()
            finally:connections['default'].close()
        with ThreadPoolExecutor(max_workers=2) as pool:return list(pool.map(lambda _:worker(),range(2)))
    def test_simultaneous_measurement_replay_creates_one_evidence_record(self):
        token=uuid.uuid4()
        def save():return pilots.save_observation(user=self.worker,pk=self.pilot.pk,client_key=token,employee=self.worker,
            phase='FOLLOWUP',observed_on=START,source_key='concurrent-register',staffed_hours=8,accepted_output=40,evidence='SYNTHETIC independent register').pk
        ids=self.parallel(save);self.assertEqual(ids[0],ids[1]);self.assertEqual(PilotObservation.objects.count(),1)
        self.assertEqual(PilotEvent.objects.filter(client_key=token).count(),1)
    def test_simultaneous_independent_acceptance_replays_once(self):
        row=pilots.save_observation(user=self.worker,pk=self.pilot.pk,client_key=uuid.uuid4(),employee=self.worker,
            phase='FOLLOWUP',observed_on=START,source_key='concurrent-review',staffed_hours=8,accepted_output=40,evidence='SYNTHETIC independent register')
        token=uuid.uuid4()
        def accept():return pilots.review_observation(user=self.manager,pk=self.pilot.pk,observation_id=row.pk,
            expected_revision=row.revision,client_key=token,action='accept').status
        self.assertEqual(self.parallel(accept),['ACCEPTED','ACCEPTED'])
        self.assertEqual(PilotEvent.objects.filter(client_key=token).count(),1)
    def test_conflicting_controls_have_one_winner_and_no_lost_revision(self):
        revision=self.pilot.revision
        def pause():
            try:
                pilots.control_pilot(user=self.manager,pk=self.pilot.pk,expected_revision=revision,client_key=uuid.uuid4(),action='pause',reason='SYNTHETIC concurrent pause')
                return 'saved'
            except ValidationError:return 'stale'
        self.assertCountEqual(self.parallel(pause),['saved','stale'])

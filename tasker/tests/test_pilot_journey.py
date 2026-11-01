from django.test import TestCase,override_settings
from .pilot_fixtures import fixtures
from .pilot_journey import run_journey,FLAGS


@override_settings(**FLAGS)
class PilotJourneyTests(TestCase):
    @classmethod
    def setUpTestData(cls):fixtures(cls,synthetic=True,measurements=False)
    def test_capture_goal_plan_focus_handoff_source_acceptance_board_and_recovery(self):
        result=run_journey(self)
        self.assertEqual(result['provider_requests'],0)
        self.assertTrue(result['native_source_accepted_once'])
        self.assertTrue(result['failed_job_recovered'])

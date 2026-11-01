import json,tempfile,io
from pathlib import Path
from unittest.mock import patch
from django.test import SimpleTestCase,override_settings
from django.core.management import call_command
from tasker.services.pilot_release import local_evidence,LOCAL_CHECKS


class LocalReleaseEvidenceTests(SimpleTestCase):
    def test_stale_or_malformed_evidence_cannot_verify_source(self):
        with tempfile.TemporaryDirectory() as folder,override_settings(BASE_DIR=Path(folder)),patch('tasker.services.pilot_release.source_fingerprint',return_value=('b'*64,12)):
            root=Path(folder)/'output';root.mkdir();path=root/'tasker-stage13-release-evidence.json'
            cases=['{broken',json.dumps({'source_digest':'a'*64,'checks':{v:'VERIFIED' for v in LOCAL_CHECKS}}),
                json.dumps({'source_digest':'b'*64,'checks':[]}),json.dumps({'source_digest':'b'*64,'checks':{'regressions':'invented'}})]
            for value in cases:
                path.write_text(value,encoding='utf-8');result=local_evidence()
                self.assertTrue(any(v!='VERIFIED' for v in result['checks'].values()))
            path.write_text(json.dumps({'source_digest':'b'*64,'checks':{v:'VERIFIED' for v in LOCAL_CHECKS}}),encoding='utf-8')
            self.assertTrue(local_evidence()['current'])
            self.assertTrue(all(v=='VERIFIED' for v in local_evidence()['checks'].values()))

    def test_unreadable_or_changing_source_stays_pending(self):
        with patch('tasker.services.pilot_release.source_fingerprint',side_effect=OSError('changed during read')):
            result=local_evidence();self.assertFalse(result['current']);self.assertEqual(result['source_digest'],'')

    def test_offline_command_makes_no_database_query_or_provider_request(self):
        checks={'checks':{v:'PENDING' for v in LOCAL_CHECKS},'source_digest':'a'*64,'current':False}
        with patch('tasker.management.commands.check_tasker_release.local_evidence',return_value=checks):
            stream=io.StringIO();call_command('check_tasker_release',stdout=stream)
        report=json.loads(stream.getvalue());self.assertFalse(report['provider_request']);self.assertFalse(report['database_write'])
        self.assertEqual(report['real_productivity'],'PENDING')

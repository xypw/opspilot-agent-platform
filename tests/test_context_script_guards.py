"""Context acceptance preserves old evidence and the original shared budget."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import verify_context_deployment as deployment
from scripts import verify_context_live as live


class ContextScriptGuardsTests(unittest.TestCase):
    def setUp(self):
        temporary=TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        (self.root/'reports').mkdir()
        (self.root/'output').mkdir()
        self.ledger=self.root/'output/context-live-budget-20261023.json'
        self.lock=self.ledger.with_suffix('.lock')
        for replacement in (patch.object(live,'ROOT',self.root),
                            patch.object(deployment,'ROOT',self.root),patch.dict(os.environ,{})):
            replacement.start();self.addCleanup(replacement.stop)
        self.http_patch=patch.object(live.httpx,'Client')
        self.http=self.http_patch.start();self.addCleanup(self.http_patch.stop)
        self.http.return_value.__enter__.return_value.get.return_value.json.return_value={'version':'0.40.5'}
        self.chat_patch=patch('ollama_gateway.ollama_chat')
        self.chat=self.chat_patch.start();self.addCleanup(self.chat_patch.stop)
        self.chat.return_value={'content':'OK','_context_usage':{'prompt_tokens':9,'counted_input':9}}

    def report(self,number=1):
        return self.root/f'reports/context-live-20261023-run-{number}.json'

    def save_budget(self,charged,runs):
        self.ledger.write_text(json.dumps({'charged':charged,'runs':runs}),encoding='utf-8')

    def test_fresh_clone_cannot_overwrite_report_or_initialize_budget(self):
        self.report().write_text('historical evidence',encoding='utf-8')
        with self.assertRaises(FileExistsError):live.run(['plain'])
        self.assertEqual(self.report().read_text(encoding='utf-8'),'historical evidence')
        self.assertFalse(self.ledger.exists());self.assertFalse(self.lock.exists())
        self.http.assert_not_called();self.chat.assert_not_called()

    def test_existing_next_report_preserves_ledger_before_http(self):
        self.save_budget(11,6);before=self.ledger.read_bytes()
        self.report(7).write_text('previous attempt',encoding='utf-8')
        with self.assertRaises(FileExistsError):live.run(['plain'])
        self.assertEqual(self.ledger.read_bytes(),before)
        self.http.assert_not_called();self.chat.assert_not_called()

    def test_another_process_lock_is_not_removed(self):
        self.save_budget(11,6);before=self.ledger.read_bytes()
        self.lock.write_text('another owner',encoding='utf-8')
        with self.assertRaises(FileExistsError):live.run(['plain'])
        self.assertEqual(self.lock.read_text(encoding='utf-8'),'another owner')
        self.assertEqual(self.ledger.read_bytes(),before)
        self.http.assert_not_called();self.chat.assert_not_called()

    def test_exhausted_budget_stops_before_metadata_http(self):
        self.save_budget(12,6);before=self.ledger.read_bytes()
        with self.assertRaises(RuntimeError):live.run(['plain'])
        self.assertEqual(self.ledger.read_bytes(),before)
        self.assertFalse(self.report(7).exists());self.assertFalse(self.lock.exists())
        self.http.assert_not_called();self.chat.assert_not_called()

    def test_malformed_run_number_cannot_reset_report_sequence(self):
        for runs in (-1,True,'6'):
            with self.subTest(runs=runs):
                self.save_budget(11,runs);before=self.ledger.read_bytes()
                with self.assertRaises(RuntimeError):live.run(['plain'])
                self.assertEqual(self.ledger.read_bytes(),before)
        self.http.assert_not_called();self.chat.assert_not_called()

    def test_last_charge_is_persisted_and_concurrent_attempt_is_refused(self):
        self.save_budget(11,6)
        def infer(*args,**kwargs):
            self.assertEqual(json.loads(self.ledger.read_text()),{'charged':12,'runs':7})
            self.assertEqual(os.environ['MODEL_RATE_LIMIT_SCOPE'],'context-calibration-20261023')
            self.assertEqual(os.environ['MODEL_RATE_LIMIT_REQUESTS'],'12')
            with self.assertRaises(FileExistsError):live.run(['plain'])
            return {'content':'OK','_context_usage':{'prompt_tokens':9,'counted_input':9}}
        self.chat.side_effect=infer
        self.assertTrue(live.run(['plain']))
        self.chat.assert_called_once()
        self.assertEqual(json.loads(self.ledger.read_text()),{'charged':12,'runs':7})
        self.assertEqual(json.loads(self.report(7).read_text())['charged'],12)
        self.assertFalse(self.lock.exists())

    def test_failed_inference_keeps_charge_and_failure_evidence(self):
        self.save_budget(11,6)
        self.chat.side_effect=RuntimeError('synthetic error')
        self.assertFalse(live.run(['plain']))
        self.assertEqual(json.loads(self.ledger.read_text())['charged'],12)
        result=json.loads(self.report(7).read_text())
        self.assertFalse(result['rows'][0]['passed'])
        self.assertEqual(result['rows'][0]['error'],'RuntimeError')
        with self.assertRaises(RuntimeError):live.run(['plain'])
        self.chat.assert_called_once()

    def test_metadata_failure_preserves_reserved_report_without_charge(self):
        self.save_budget(11,6)
        self.http.return_value.__enter__.return_value.get.side_effect=RuntimeError('synthetic')
        with self.assertRaises(RuntimeError):live.run(['plain'])
        self.assertEqual(json.loads(self.ledger.read_text()),{'charged':11,'runs':7})
        self.assertEqual(json.loads(self.report(7).read_text())['error_type'],'RuntimeError')
        self.assertFalse(self.lock.exists());self.chat.assert_not_called()

    def test_deployment_existing_output_refuses_before_any_checks(self):
        target=self.root/'reports/context-deployment-20261024.json'
        target.write_text('historical report',encoding='utf-8')
        with patch.object(deployment,'_run_checks') as checks:
            with self.assertRaises(FileExistsError):deployment.run()
            checks.assert_not_called()
        self.assertEqual(target.read_text(encoding='utf-8'),'historical report')
        self.http.assert_not_called()

    def test_deployment_new_output_is_reserved_before_checks(self):
        target=self.root/'reports/new-deployment.json'
        result={'checks_passed':13,'llm_requests':0}
        def checks():
            self.assertTrue(target.exists())
            with self.assertRaises(FileExistsError):deployment.run(target)
            return result
        with patch.object(deployment,'_run_checks',side_effect=checks) as execute:
            self.assertEqual(deployment.run(target),result)
            execute.assert_called_once()
        self.assertEqual(json.loads(target.read_text()),result)

    def test_failed_deployment_does_not_erase_evidence_or_run_again(self):
        target=self.root/'reports/failed-deployment.json'
        with patch.object(deployment,'_run_checks',side_effect=ValueError('synthetic')) as checks:
            with self.assertRaises(ValueError):deployment.run(target)
            with self.assertRaises(FileExistsError):deployment.run(target)
            checks.assert_called_once()
        self.assertEqual(json.loads(target.read_text()),
                         {'passed':False,'llm_requests':0,'error_type':'ValueError'})

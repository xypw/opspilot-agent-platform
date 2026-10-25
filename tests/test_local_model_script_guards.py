"""验收脚本本身不能意外发起未授权推理；所有HTTP均被替换。"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock,patch

from scripts.diagnose_local_evidence import run
from scripts.smoke_customer_portal import verify_external_consent
from scripts.verify_local_policy_fix import valid_policy_result


class ScriptGuardsTests(unittest.TestCase):
    def test_policy_check_uses_public_filtered_tool_result_and_requires_exact_quote(self):
        result={"status":"COMPLETED","result":{"tool_name":"search_knowledge_base",
            "tool_result":[{"content":"三个工作日到账","page":1}],
            "answer":"三个工作日到账\n来源：《虚构政策》第1页"}}
        self.assertTrue(valid_policy_result(result))
        result["result"]["answer"]="来源：《虚构政策》第1页，明天到账"
        self.assertFalse(valid_policy_result(result))

    def test_local_mock_smoke_never_calls_live_consent_probe(self):
        probe,check=Mock(),Mock()
        verify_external_consent({"location":"local","requires_image_consent":False},probe,check)
        probe.assert_not_called()
        check.assert_called_once_with("local_consent_probe_skipped_no_model_call",True)

    def test_cloud_probe_only_checks_rejection_and_invalid_config_stops(self):
        probe,check=Mock(),Mock()
        verify_external_consent({"location":"cloud","requires_image_consent":True},probe,check)
        probe.assert_called_once()
        probe.reset_mock()
        with self.assertRaises(RuntimeError):
            verify_external_consent({"location":"cloud","requires_image_consent":False},probe,check)
        probe.assert_not_called()

    def fixture(self,root):
        (root/"reports").mkdir();(root/"output").mkdir()
        ledger=root/"output/local-model-budget-20261022.json"
        ledger.write_text(json.dumps({"charged":3}),encoding="utf-8")
        return ledger,root/"reports/local-model-evidence-diagnostic-20261022.json"

    def test_existing_report_refuses_before_budget_or_http_changes(self):
        with TemporaryDirectory() as directory,patch("scripts.diagnose_local_evidence.httpx.Client") as http:
            root=Path(directory);ledger,target=self.fixture(root)
            target.write_text("historical-report",encoding="utf-8")
            original=ledger.read_bytes()
            with self.assertRaises(FileExistsError):run(root)
            http.assert_not_called()
            self.assertEqual(ledger.read_bytes(),original)
            self.assertEqual(target.read_text(encoding="utf-8"),"historical-report")

    def test_shared_budget_cannot_be_bypassed_by_direct_diagnostic(self):
        with TemporaryDirectory() as directory,patch("scripts.diagnose_local_evidence.httpx.Client") as http:
            root=Path(directory);ledger,target=self.fixture(root)
            ledger.write_text(json.dumps({"charged":3,"redis_scope":"acceptance"}),encoding="utf-8")
            with self.assertRaises(RuntimeError):run(root)
            http.assert_not_called()
            self.assertEqual(json.loads(ledger.read_text())["charged"],3)
            self.assertEqual(json.loads(target.read_text())["http_attempts"],0)

    def test_failed_request_keeps_budget_and_cannot_repeat(self):
        with TemporaryDirectory() as directory,patch("scripts.diagnose_local_evidence.httpx.Client") as http:
            root=Path(directory);ledger,target=self.fixture(root)
            http.return_value.__enter__.return_value.post.side_effect=RuntimeError("synthetic failure")
            with self.assertRaises(RuntimeError):run(root)
            self.assertEqual(json.loads(ledger.read_text())["charged"],4)
            with self.assertRaises(FileExistsError):run(root)
            self.assertEqual(http.call_count,1)
            self.assertEqual(json.loads(target.read_text())["error_type"],"RuntimeError")

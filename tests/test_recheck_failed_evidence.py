import unittest
from unittest.mock import Mock

from preview_tool_call import ModelAPIError
from scripts.recheck_failed_evidence import run_cases


class FailureRecheckTests(unittest.TestCase):
    def test_stops_after_exhausted_429_and_does_not_claim_remaining_cases_ran(self):
        cases = [{"case_id": str(i), "question": "退款多久到账？", "sufficient": True,
                  "candidate": {"chunk_id": "r", "content": "退款三个工作日到账。"}} for i in range(3)]
        error = ModelAPIError(429, "1302")
        error.model_http_attempts = 3
        reviewer = Mock(side_effect=error)
        results = run_cases(cases, reviewer, pause=Mock())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["model_http_attempts"], 3)
        reviewer.assert_called_once()

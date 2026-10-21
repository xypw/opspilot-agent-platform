"""分类政策的跨服务协议；所有 HTTP 均由 MockTransport 响应。"""

import unittest
import httpx
from pydantic import ValidationError
from order_service_client import JavaOrderClient, OrderServiceError, ReturnEligibilityResponse
from return_review_client import review_return


class CategoryReturnContractTests(unittest.TestCase):
    def test_agent_text_does_not_turn_category_or_merchant_result_into_seven_day_claim(self):
        from agent_graph import ConfiguredAgentModelGateway
        for decision, reason, days in [
            ("NO_REASON_ALLOWED", "WITHIN_MERCHANT_NO_REASON_WINDOW", 20),
            ("REASON_REQUIRED", "NO_REASON_CATEGORY_EXCLUDED", 1),
            ("REASON_REQUIRED", "POLICY_FACTS_REQUIRED", 1),
        ]:
            with self.subTest(reason=reason):
                answer = ConfiguredAgentModelGateway._build_mock_answer([
                    ("check_return_eligibility", self.eligibility(decision, reason, days))])
                self.assertNotIn("七天", answer)
                if decision == "REASON_REQUIRED":
                    self.assertIn("尚未批准", answer)

    def eligibility(self, decision, reason, days):
        return {"order_id": "O-7201", "decision": decision,
                "can_apply": True, "reason_required": decision == "REASON_REQUIRED",
                "days_since_delivery": days, "reason": reason}

    def test_category_and_missing_facts_results_are_accepted_even_within_seven_days(self):
        for reason in ("NO_REASON_CATEGORY_EXCLUDED", "POLICY_FACTS_REQUIRED",
                       "PRODUCT_CONDITION_UNKNOWN", "PRODUCT_NOT_INTACT", "EXCLUSION_CONFIRMATION_REQUIRED"):
            with self.subTest(reason=reason), httpx.Client(base_url="http://java.invalid", transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, json=self.eligibility("REASON_REQUIRED", reason, 1)))) as http:
                self.assertEqual(JavaOrderClient(http).get_return_eligibility("O-7201")["reason"], reason)

    def test_merchant_extension_and_late_quality_review_are_not_rejected_by_old_day_limit(self):
        for decision, reason, days in [("NO_REASON_ALLOWED", "WITHIN_MERCHANT_NO_REASON_WINDOW", 20),
                                       ("REASON_REQUIRED", "NO_REASON_WINDOW_EXPIRED", 90)]:
            with self.subTest(reason=reason):
                self.assertEqual(ReturnEligibilityResponse.model_validate(
                    self.eligibility(decision, reason, days)).decision, decision)

    def test_excluded_reason_cannot_be_paired_with_automatic_approval(self):
        with self.assertRaises(ValidationError):
            ReturnEligibilityResponse.model_validate(
                self.eligibility("NO_REASON_ALLOWED", "NO_REASON_CATEGORY_EXCLUDED", 1))

    def test_missing_facts_are_manual_review_not_rejection(self):
        for reason in ("POLICY_FACTS_REQUIRED", "PRODUCT_CONDITION_UNKNOWN", "EXCLUSION_CONFIRMATION_REQUIRED"):
            with self.subTest(reason=reason), httpx.Client(base_url="http://java.invalid", transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, json={"order_id": "O-7201", "decision": "MANUAL_REVIEW", "reason": reason}))) as http:
                self.assertEqual(review_return(http, "O-7201", "不喜欢", "PERSONAL_PREFERENCE")["decision"], "MANUAL_REVIEW")

    def test_contradictory_manual_review_reply_fails_closed(self):
        with httpx.Client(base_url="http://java.invalid", transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"order_id": "O-7201", "decision": "ACCEPTABLE", "reason": "POLICY_FACTS_REQUIRED"}))) as http:
            with self.assertRaises(OrderServiceError):
                review_return(http, "O-7201", "不喜欢", "PERSONAL_PREFERENCE")

    def test_personal_category_rejection_is_preserved(self):
        with httpx.Client(base_url="http://java.invalid", transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"order_id": "O-7201", "decision": "REJECTED", "reason": "NO_REASON_CATEGORY_EXCLUDED"}))) as http:
            self.assertEqual(review_return(http, "O-7201", "不喜欢", "PERSONAL_PREFERENCE")["decision"], "REJECTED")


if __name__ == "__main__":
    unittest.main()

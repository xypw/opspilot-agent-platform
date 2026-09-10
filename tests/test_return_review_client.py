import unittest
import httpx
from return_review_client import review_return
from order_service_client import OrderServiceError


class ReturnReviewClientTests(unittest.TestCase):
    def test_posts_reason_and_validates_business_result(self):
        def handler(request):
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, "/api/orders/O-2001/return-review")
            import json
            self.assertEqual(json.loads(request.content),
                             {"return_reason": "按键失灵", "reason_code": "QUALITY_ISSUE"})
            return httpx.Response(200, json={"order_id": "O-2001", "decision": "MANUAL_REVIEW",
                                            "reason": "EVIDENCE_REVIEW_REQUIRED"})
        with httpx.Client(base_url="http://java.test", transport=httpx.MockTransport(handler)) as http:
            self.assertEqual(review_return(http, "O-2001", "按键失灵", "QUALITY_ISSUE")["decision"],
                             "MANUAL_REVIEW")

    def test_failures_or_contradictions_cannot_become_acceptance(self):
        responses = [
            httpx.Response(500, text="PRIVATE"),
            httpx.Response(404, json={"code": "ORDER_NOT_FOUND"}),
            httpx.Response(200, json={"order_id": "O-2002", "decision": "ACCEPTABLE",
                                     "reason": "NO_REASON_WINDOW"}),
            httpx.Response(200, json={"order_id": "O-2001", "decision": "ACCEPTABLE",
                                     "reason": "EVIDENCE_REVIEW_REQUIRED"}),
        ]
        for response in responses:
            with self.subTest(status=response.status_code):
                with httpx.Client(base_url="http://java.test",
                                  transport=httpx.MockTransport(lambda r: response)) as http:
                    with self.assertRaises(OrderServiceError):
                        review_return(http, "O-2001", "原因", "OTHER")

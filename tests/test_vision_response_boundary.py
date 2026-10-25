"""A successful HTTP response is not proof of a complete visual answer."""
import json
import os
import unittest
from unittest.mock import patch

import httpx

from vision_service import ConfiguredVisionGateway


class VisionResponseBoundaryTests(unittest.TestCase):
    def call_with(self, payload):
        client = httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=payload)))
        with patch.dict(os.environ, {"OPSPILOT_MODEL_PROVIDER": "zhipu",
                                    "VISION_ENABLED": "true", "VISION_MODEL": "glm-4.6v-flash"}), \
                patch("vision_service.httpx.Client", return_value=client), \
                patch("vision_service.load_api_key", return_value="fixture-key-not-real"), \
                patch("vision_service.acquire_model_request") as quota:
            try:
                return ConfiguredVisionGateway()("图片颜色", [
                    {"content": b"synthetic-image", "mime_type": "image/jpeg"}], [], "live")
            finally:
                quota.assert_called_once()

    def test_incomplete_answer_rejected_even_if_json_is_valid(self):
        for reason in ("length", "content_filter", "sensitive"):
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, "受控问答格式"):
                self.call_with({"choices": [{"finish_reason": reason, "message": {"content":
                    json.dumps({"answer": "红色", "observations": ["红色"], "uncertainty": "无法确认材质"})}}]})

    def test_malformed_envelopes_raise_known_format_error(self):
        for payload in (None, [], {"choices": []}, {"choices": [None]},
                        {"choices": [{"message": []}]}, {"choices": [{"message": None}]}):
            with self.subTest(payload=payload), self.assertRaisesRegex(ValueError, "受控问答格式"):
                self.call_with(payload)

    def test_complete_answer_is_accepted(self):
        result = self.call_with({"choices": [{"finish_reason": "stop", "message": {"content":
            json.dumps({"answer": "红色", "observations": ["红色"], "uncertainty": "无法确认材质"})}}]})
        self.assertEqual(result["answer"], "红色")
        self.assertEqual(result["model_http_attempts"], 1)

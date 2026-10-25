"""Local asset failures must never look like unknown business writes."""

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from model_context import ContextBudgetExceeded
from ollama_gateway import ollama_chat
from qwen_tokenizer import load_tokenizer


LOCAL = {"OPSPILOT_MODEL_PROVIDER": "ollama", "OLLAMA_BASE_URL": "http://127.0.0.1:11434",
         "OLLAMA_MODEL": "qwen3-vl:4b", "QWEN_TOKENIZER_PATH": ""}


class ContextFailureBoundaryTests(unittest.TestCase):
    def setUp(self):
        # Keep nested portal fixture cleanups inside this environment lifetime.
        # A method decorator would restore too early, before addCleanup runs.
        self.enterContext(patch.dict(os.environ, LOCAL))

    def test_invalid_manifest_shape_is_preflight_failure_without_http_or_quota(self):
        for manifest in (None, [], "not a manifest"):
            with self.subTest(manifest=manifest), tempfile.TemporaryDirectory() as directory:
                Path(directory, "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
                client = Mock()
                with patch.dict(os.environ, {"QWEN_TOKENIZER_PATH": directory}), patch(
                        "ollama_gateway.acquire_model_request") as quota:
                    with self.assertRaises(ContextBudgetExceeded):
                        ollama_chat(client, [{"role": "user", "content": "退款政策？"}])
                quota.assert_not_called()
                client.stream.assert_not_called()

    def test_invalid_tokenizer_schema_is_preflight_failure_without_http_or_quota(self):
        with tempfile.TemporaryDirectory() as directory:
            # Valid JSON and a matching checksum still need tokenizer schema validation.
            content = b"{}"
            Path(directory, "tokenizer.json").write_bytes(content)
            manifest = {"model": "qwen3-vl:4b", "pre_tokenizer": "qwen2",
                        "renderer": "qwen3-vl-thinking", "ollama_version": "0.40.9",
                        "sha256": hashlib.sha256(content).hexdigest()}
            Path(directory, "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            client = Mock()
            with patch.dict(os.environ, {"QWEN_TOKENIZER_PATH": directory}), patch(
                    "ollama_gateway.acquire_model_request") as quota:
                with self.assertRaises(ContextBudgetExceeded):
                    ollama_chat(client, [{"role": "user", "content": "退款政策？"}])
            quota.assert_not_called()
            client.stream.assert_not_called()

    def test_asset_failure_releases_portal_turn_instead_of_blocking_as_unknown(self):
        from tests.test_customer_portal import PortalAuthenticationTests

        fixture = PortalAuthenticationTests(methodName="test_cookie_requires_csrf_for_mutations")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.login()
        cid = fixture.store.create_conversation("U-1001", "asset-failure")["id"]
        client = Mock()
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "manifest.json").write_text("null", encoding="utf-8")
            with patch.dict(os.environ, {**LOCAL, "QWEN_TOKENIZER_PATH": directory,
                                          "MEMORY_EMBEDDING_PATH": ""}), patch(
                    "main.start_agent_graph", side_effect=lambda *args, **kwargs: ollama_chat(
                        client, [{"role": "user", "content": "退款政策？"}])), patch(
                    "ollama_gateway.acquire_model_request") as quota:
                response = fixture.client.post("/conversations/" + cid + "/turns",
                    headers=fixture.csrf(), json={"request_id": str(uuid4()),
                                                 "message": "退款政策？", "mode": "live"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(fixture.store.conversation("U-1001", cid)["turns"][0]["status"], "FAILED")
        self.assertIsNone(fixture.store.conversations[cid]["active_turn_id"])
        quota.assert_not_called()
        client.stream.assert_not_called()

    def tearDown(self):
        load_tokenizer.cache_clear()

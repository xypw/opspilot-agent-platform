"""Public recovery correlation must survive the production serializer."""
import unittest

from portal_store import public_turn


class PortalResponseContractTests(unittest.TestCase):
    def test_request_id_is_public_but_payload_hash_is_private(self):
        row = {"id": "turn", "request_id": "client-key", "request_hash": "private", "status": "COMPLETED"}
        result = public_turn(row)
        self.assertEqual(result["request_id"], "client-key")
        self.assertNotIn("request_hash", result)
        self.assertIn("request_hash", row)

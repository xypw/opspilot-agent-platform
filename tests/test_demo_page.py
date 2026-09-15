"""演示页面必须由同一 FastAPI 服务提供，样例文件可下载。"""

import unittest
from io import BytesIO

from fastapi.testclient import TestClient

from document_parser import parse_pdf
from main import app


class DemoPageTests(unittest.TestCase):
    def test_demo_and_assets_are_served_from_same_origin(self):
        client = TestClient(app)
        page = client.get("/demo")
        script = client.get("/demo-assets/demo.js")
        styles = client.get("/demo-assets/demo.css")

        self.assertEqual(page.status_code, 200)
        self.assertIn('id="approval-details"', page.text)
        self.assertIn('id="restore-form"', page.text)
        self.assertEqual(script.status_code, 200)
        self.assertIn("function actionsFor", script.text)
        self.assertEqual(styles.status_code, 200)
        self.assertIn("text", styles.headers["content-type"])

    def test_sample_policy_is_real_pdf(self):
        response = TestClient(app).get("/demo/sample-policy.pdf")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertIn("application/pdf", response.headers["content-type"])
        pages = parse_pdf(BytesIO(response.content))
        self.assertEqual(len(pages), 1)
        content = pages[0]["text"]
        self.assertIn("普通退款", content)
        self.assertIn("3 个工作日", content)
        self.assertIn("紧急退款", content)
        self.assertIn("3 小时", content)

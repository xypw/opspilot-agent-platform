"""本机 PostgreSQL 入库审核验收；只创建并清理本次随机测试文档。"""

from io import BytesIO
import json
import os
from pathlib import Path
from uuid import uuid4

from dotenv import dotenv_values
import httpx
import psycopg
from pypdf import PdfReader, PdfWriter


ROOT = Path(__file__).resolve().parents[1]


def _synthetic_pdf() -> bytes:
    writer = PdfWriter()
    writer.append(PdfReader(ROOT / "sample_documents/opspilot_demo_policy.pdf"))
    writer.add_blank_page(width=612, height=792)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def main() -> None:
    configured = json.loads(os.environ["OPSPILOT_AUTH_TOKENS"])
    token = next(key for key, value in configured.items()
                 if "knowledge_admin" in value.get("roles", []))
    database_url = os.getenv("DATABASE_URL") or dotenv_values(ROOT / ".env")["DATABASE_URL"]
    document_id = "review-smoke-" + uuid4().hex[:12]
    summary = {"document_id": document_id, "external_model_requests": 0}

    with psycopg.connect(database_url) as database:
        with database.cursor() as cursor:
            cursor.execute("SELECT 1 FROM documents WHERE id = %s", (document_id,))
            assert cursor.fetchone() is None
            cursor.execute("SELECT 1 FROM document_review_queue WHERE id = %s", (document_id,))
            assert cursor.fetchone() is None

        try:
            with httpx.Client(base_url="http://127.0.0.1:8011", timeout=10,
                              trust_env=False) as anonymous:
                blocked = anonymous.post(
                    "/documents/upload",
                    data={"document_id": document_id, "title": "虚构售后制度验收"},
                    files={"file": ("review-smoke.pdf", _synthetic_pdf(), "application/pdf")},
                )
                assert blocked.status_code == 401
                assert anonymous.get(f"/documents/{document_id}/review").status_code == 401
                summary["unauthenticated_upload_and_review_blocked"] = True
            with httpx.Client(base_url="http://127.0.0.1:8011", timeout=30,
                              trust_env=False, headers={"Authorization": "Bearer " + token}) as client:
                upload = client.post(
                    "/documents/upload",
                    data={"document_id": document_id, "title": "虚构售后制度验收"},
                    files={"file": ("review-smoke.pdf", _synthetic_pdf(), "application/pdf")},
                )
                assert upload.status_code == 200, ("upload", upload.status_code, upload.text)
                assert upload.json()["publication_status"] == "pending_review"
                assert upload.json()["ocr_required_pages"] == [2]
                query = "紧急退款多久到账？"
                summary["upload_quarantined"] = True

                with database.cursor() as cursor:
                    cursor.execute("SELECT count(*) FROM knowledge_chunks WHERE document_id = %s",
                                   (document_id,))
                    assert cursor.fetchone()[0] == 0
                    cursor.execute("SELECT count(*) FROM document_review_queue WHERE id = %s",
                                   (document_id,))
                    assert cursor.fetchone()[0] == 1
                before = client.post("/knowledge/search", json={"query": query, "limit": 5})
                assert before.status_code == 200, ("search_before", before.status_code, before.text)
                assert all(not item["chunk_id"].startswith(document_id + "-")
                           for item in before.json())
                summary["unapproved_not_searchable"] = True

                review = client.get(f"/documents/{document_id}/review")
                source = client.get(f"/documents/{document_id}/source")
                assert review.status_code == source.status_code == 200
                assert len(review.json()["result"]["pages"]) == 2
                assert source.content.startswith(b"%PDF")
                summary["original_and_review_visible_to_admin"] = True

                published = client.post(f"/documents/{document_id}/publish", json={"pages": [1]})
                assert published.status_code == 200, ("publish", published.status_code, published.text)
                assert published.json()["published_pages"] == [1]
                assert published.json()["remaining_pages"] == [2]
                with database.cursor() as cursor:
                    cursor.execute("SELECT count(*) FROM knowledge_chunks WHERE document_id = %s "
                                   "AND publication_status = 'published'", (document_id,))
                    assert cursor.fetchone()[0] > 0
                    cursor.execute("SELECT count(*) FROM document_review_events WHERE document_id = %s",
                                   (document_id,))
                    assert cursor.fetchone()[0] == 1
                after = client.post("/knowledge/search", json={"query": query, "limit": 5})
                assert after.status_code == 200, ("search_after", after.status_code, after.text)
                assert any(item["chunk_id"].startswith(document_id + "-")
                           for item in after.json()), (
                               "已批准片段未进入检索结果",
                               [(item["chunk_id"], round(item["score"], 3)) for item in after.json()],
                           )
                summary["approved_page_searchable"] = True

                repeated = client.post(f"/documents/{document_id}/publish", json={"pages": [1]})
                blank = client.post(f"/documents/{document_id}/publish", json={"pages": [2]})
                assert repeated.status_code == blank.status_code == 409
                summary["reapproval_and_empty_page_blocked"] = True
        finally:
            with database.cursor() as cursor:
                cursor.execute("DELETE FROM documents WHERE id = %s", (document_id,))
                cursor.execute("DELETE FROM document_review_queue WHERE id = %s", (document_id,))
            database.commit()
            summary["test_document_cleaned"] = True

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

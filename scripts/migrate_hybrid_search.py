"""升级词法索引并分批回填已发布片段；不调用向量或重排模型。"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import create_database_connection  # noqa: E402
from keyword_search import search_terms  # noqa: E402


def migrate(connection_factory=create_database_connection, batch_size: int = 200) -> dict:
    if not 1 <= batch_size <= 1000:
        raise ValueError("batch_size 必须在 1 到 1000 之间")
    with connection_factory() as connection:
        connection.execute((ROOT / "db/migrations/002_hybrid_retrieval.sql").read_text(encoding="utf-8"))
    updated = 0
    last_id = ""
    while True:
        with connection_factory() as connection:
            rows = connection.execute(
                """SELECT c.id, d.title, c.content
                     FROM knowledge_chunks c JOIN documents d ON d.id = c.document_id
                    WHERE c.id > %s AND cardinality(c.lexical_terms) = 0
                      AND c.publication_status = 'published'
                      AND d.publication_status IN ('published', 'partial_published')
                    ORDER BY c.id LIMIT %s""", (last_id, batch_size),
            ).fetchall()
            if not rows:
                break
            for chunk_id, title, content in rows:
                # 内容若被并发修改则不以旧正文回填，重跑脚本可重新处理。
                updated += connection.execute(
                    """UPDATE knowledge_chunks SET lexical_terms = %s
                       WHERE id = %s AND content = %s AND cardinality(lexical_terms) = 0""",
                    (sorted(search_terms(f"{title} {content}")), chunk_id, content),
                ).rowcount
            last_id = rows[-1][0]
    return {"updated_chunks": updated, "external_model_calls": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=200)
    args = parser.parse_args()
    print(json.dumps(migrate(batch_size=args.batch_size)))

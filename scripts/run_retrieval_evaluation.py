"""从命令行运行 OpsPilot 的真实检索评测。"""

import argparse
import copy
import json
import sys
from pathlib import Path

# 直接执行 ``python scripts/run_retrieval_evaluation.py`` 时，Python 只会把
# scripts 目录加入导入路径。先加入项目根目录，下面才能导入同级业务模块。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from embedding_service import LocalEmbeddingService, attach_embeddings
from retrieval_evaluation import compare_retrievers
from siliconflow_reranker import build_configured_reranker


CASES_FILE = PROJECT_ROOT / "evaluation_data" / "retrieval_cases.json"
DOCUMENTS_FILE = PROJECT_ROOT / "evaluation_data" / "retrieval_documents.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="运行 OpsPilot 检索评测")
    parser.add_argument(
        "--skip-reranker",
        action="store_true",
        help="只运行本地语义检索和混合检索，不调用云端 Reranker",
    )
    args = parser.parse_args()

    # 固定 cases 文件是评测资产：不要在某个方案表现差时悄悄改标准答案。
    cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    documents = json.loads(DOCUMENTS_FILE.read_text(encoding="utf-8"))

    # 评测语料与产品内置演示数据分开，避免为了“刷指标”修改实际功能的数据。
    # attach_embeddings 会为字典加 embedding，因此深拷贝保护原始 JSON 数据结构。
    embedding_service = LocalEmbeddingService()
    records = attach_embeddings(copy.deepcopy(documents), embedding_service)
    comparison = compare_retrievers(
        cases,
        records,
        embedding_service,
        # 基线评测无需网络；只有显式启用时才读取 Key 并调用云端模型。
        reranker=None if args.skip_reranker else build_configured_reranker(),
        k=3,
    )
    print(json.dumps(comparison, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

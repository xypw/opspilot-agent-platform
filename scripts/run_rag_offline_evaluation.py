"""复跑固定语料上的检索、证据准入和来源引用三层离线评测。"""

import argparse
import copy
import json
import os
import sys
from pathlib import Path


# 本脚本只使用本地已缓存的 Embedding；不下载模型或调用云端 Reranker。
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from citation_evaluation import evaluate_citation_sources  # noqa: E402
from embedding_service import LocalEmbeddingService, attach_embeddings  # noqa: E402
from evidence_sufficiency_evaluation import evaluate_evidence_sufficiency  # noqa: E402
from evidence_support import filter_evidence_for_question_v1  # noqa: E402
from retrieval_evaluation import compare_retrievers  # noqa: E402


RETRIEVAL_CASES = "evaluation_data/retrieval_cases.json"
RETRIEVAL_DOCUMENTS = "evaluation_data/retrieval_documents.json"
EVIDENCE_CASES = "evaluation_data/evidence_sufficiency_cases.json"
EVIDENCE_CHALLENGE_CASES = "evaluation_data/evidence_sufficiency_challenge_cases.json"
DEFAULT_REPORT = "reports/rag-offline-20260914.json"


def _load_json(relative_path: str) -> list[dict]:
    return json.loads((PROJECT_ROOT / relative_path).read_text(encoding="utf-8"))


def _with_gate_rates(result: dict) -> dict:
    """在原始混淆矩阵上补充可直接比较的门禁指标。"""
    counts = result["counts"]
    positive_count = counts["tp"] + counts["fn"]
    negative_count = counts["fp"] + counts["tn"]
    total = positive_count + negative_count
    return {
        **result,
        "false_rejection_rate": counts["fn"] / positive_count,
        "false_admission_rate": counts["fp"] / negative_count,
        "precision": counts["tp"] / (counts["tp"] + counts["fp"]),
        "recall": counts["tp"] / positive_count,
        "accuracy": (counts["tp"] + counts["tn"]) / total,
    }


def build_report() -> dict:
    cases = _load_json(RETRIEVAL_CASES)
    documents = _load_json(RETRIEVAL_DOCUMENTS)
    evidence_cases = _load_json(EVIDENCE_CASES)
    evidence_challenge_cases = _load_json(EVIDENCE_CHALLENGE_CASES)

    embedding = LocalEmbeddingService()
    records = attach_embeddings(copy.deepcopy(documents), embedding)
    retrieval = compare_retrievers(cases, records, embedding, reranker=None, k=3)
    hybrid = next(item for item in retrieval if item["retriever"] == "hybrid_rrf")
    evidence_baseline = _with_gate_rates(evaluate_evidence_sufficiency(
        evidence_cases, filter_evidence_for_question_v1
    ))
    evidence = _with_gate_rates(evaluate_evidence_sufficiency(evidence_cases))
    evidence_challenge_baseline = _with_gate_rates(evaluate_evidence_sufficiency(
        evidence_challenge_cases, filter_evidence_for_question_v1
    ))
    evidence_challenge = _with_gate_rates(
        evaluate_evidence_sufficiency(evidence_challenge_cases)
    )
    citation = evaluate_citation_sources(hybrid["runs"], documents, k=3)

    return {
        "mode": "offline_cached_embedding_no_llm_no_reranker",
        "datasets": {
            "retrieval_cases": RETRIEVAL_CASES,
            "retrieval_documents": RETRIEVAL_DOCUMENTS,
            "evidence_cases": EVIDENCE_CASES,
            "evidence_challenge_cases": EVIDENCE_CHALLENGE_CASES,
        },
        "scope": {
            "retrieval": "11条固定正例上的本地语义/混合检索，不含负例或真实企业流量",
            "evidence": "人工给定单个候选片段，测试当前准入规则；不是端到端检索结果",
            "citation": "混合检索Top3经生产引用格式化器输出；严格以每题单一标准来源核对，不评价模型答案语义",
        },
        "retrieval": retrieval,
        "evidence_sufficiency_baseline": evidence_baseline,
        "evidence_sufficiency": evidence,
        "evidence_sufficiency_challenge_baseline": evidence_challenge_baseline,
        "evidence_sufficiency_challenge": evidence_challenge,
        "citation_sources": citation,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="复跑 OpsPilot 的离线 RAG 多阶段评测")
    parser.add_argument("--output", default=DEFAULT_REPORT, help="输出 JSON 报告路径")
    args = parser.parse_args()
    output = Path(args.output)
    if not output.is_absolute():
        output = PROJECT_ROOT / output

    report = build_report()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    hybrid = next(item for item in report["retrieval"] if item["retriever"] == "hybrid_rrf")
    print(json.dumps({
        "report": str(output),
        "retrieval_recall_at_3": hybrid["recall_at_k"],
        "evidence_counts": report["evidence_sufficiency"]["counts"],
        "gold_source_precision": report["citation_sources"]["gold_source_precision"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

"""OpsPilot 的最小知识库检索基线。

已上传文档走“向量召回 + 关键词召回 + RRF 融合 + 可选 Reranker”。后续替换为
PostgreSQL 全文检索和 pgvector 时，仍保持相同的返回结构。
"""

import logging
import math
import os
from pathlib import Path
from threading import RLock

import psycopg
from dotenv import dotenv_values

from database import DatabaseConfigurationError
from embedding_service import LocalEmbeddingService
from evidence_support import filter_evidence_for_question
from hybrid_search import hybrid_search
from keyword_search import keyword_search
from pgvector_store import DocumentIdConflictError, PostgresVectorStore
from reranker import RerankerUnavailableError
from siliconflow_reranker import build_configured_reranker
from vector_store import InMemoryVectorStore


KNOWLEDGE_CHUNKS = [
    {
        "chunk_id": "refund-policy-p2-c1",
        "title": "售后与退款制度",
        "page": 2,
        "content": "退款申请审核通过后，款项通常会在三个工作日内原路到账。",
    },
    {
        "chunk_id": "ticket-sla-p4-c1",
        "title": "客户支持服务规范",
        "page": 4,
        "content": "高优先级工单应在二十分钟内首次响应，并持续记录处理进展。",
    },
    {
        "chunk_id": "invoice-guide-p1-c1",
        "title": "电子发票申请指南",
        "page": 1,
        "content": "订单完成后可申请电子发票，发票会发送到用户填写的邮箱。",
    },
]

# 当前单进程教学版共享这个对象；后续替换为 PostgreSQL/pgvector。
VECTOR_STORE = InMemoryVectorStore(LocalEmbeddingService())
POSTGRES_VECTOR_STORE = PostgresVectorStore(VECTOR_STORE.embedding_service)
# 没有配置免费 Reranker Key 时值为 None，混合检索会安全地停留在 RRF 排名。
RERANKER = build_configured_reranker()
LOGGER = logging.getLogger(__name__)
_REVIEW_LOCK = RLock()
_PENDING_DOCUMENTS: dict[str, dict] = {}


class DuplicateDocumentError(RuntimeError):
    """文档或片段编号与现有记录冲突。"""


class KnowledgeStoreUnavailableError(RuntimeError):
    """持久化知识库当前无法完成读取或写入。"""


class ReviewDocumentNotFoundError(LookupError):
    """待审核文档不存在。"""


class ReviewConflictError(ValueError):
    """页码无效、重复审批或审核期间文档发生变化。"""


def load_knowledge_store_backend() -> str:
    """显式选择上传存储；默认内存，不让离线测试意外连接数据库。"""
    values = dotenv_values(Path(__file__).with_name(".env"), interpolate=False)
    backend = (os.getenv("KNOWLEDGE_STORE_BACKEND")
               or values.get("KNOWLEDGE_STORE_BACKEND") or "memory").strip().lower()
    if backend not in {"memory", "postgres"}:
        raise ValueError("KNOWLEDGE_STORE_BACKEND 只能是 memory 或 postgres")
    return backend


KNOWLEDGE_STORE_BACKEND = load_knowledge_store_backend()


def load_min_semantic_similarity() -> float:
    """读取语义召回准入阈值；当前默认值仅供实验，仍需用标注集校准。"""
    values = dotenv_values(Path(__file__).with_name(".env"), interpolate=False)
    raw_value = os.getenv("KNOWLEDGE_MIN_COSINE_SIMILARITY")
    if raw_value is None:
        raw_value = values.get("KNOWLEDGE_MIN_COSINE_SIMILARITY")
    try:
        threshold = float(raw_value if raw_value is not None else "0.60")
    except (TypeError, ValueError) as error:
        raise ValueError("KNOWLEDGE_MIN_COSINE_SIMILARITY 必须是数字") from error
    if not math.isfinite(threshold) or not -1 <= threshold <= 1:
        raise ValueError("KNOWLEDGE_MIN_COSINE_SIMILARITY 必须在 -1 到 1 之间")
    return threshold


MIN_SEMANTIC_SIMILARITY = load_min_semantic_similarity()


def index_knowledge_chunks(chunks: list[dict]) -> None:
    """上传文档后，把切分结果写入共享向量库。"""
    VECTOR_STORE.add(chunks)


def index_uploaded_document(result: dict, source_filename: str, source_pdf: bytes | None = None) -> None:
    """把解析后的文档交给显式选择的后端；不把数据库错误伪装为 PDF 错误。"""
    if result.get("status", "ready") != "ready" or any(
        page.get("validation_status", "validated") != "validated"
        for page in result.get("pages", [])
    ) or any(
        chunk.get("validation_status", "validated") != "validated"
        for chunk in result["chunks"]
    ):
        raise ReviewConflictError("未通过校验的文档不能直接发布")
    if KNOWLEDGE_STORE_BACKEND == "memory":
        with _REVIEW_LOCK:
            if result["document_id"] in _PENDING_DOCUMENTS:
                raise DuplicateDocumentError("文档编号已存在于待审核区")
            index_knowledge_chunks(result["chunks"])
        return
    try:
        kwargs = dict(
            document_id=result["document_id"],
            title=result["title"],
            source_filename=source_filename,
            page_count=result["page_count"],
            chunks=result["chunks"],
        )
        if source_pdf is not None:
            kwargs.update(source_pdf=source_pdf, pages=result.get("pages", []))
        POSTGRES_VECTOR_STORE.save_document(**kwargs)
    except (psycopg.errors.UniqueViolation, DocumentIdConflictError) as error:
        raise DuplicateDocumentError("文档或片段编号已存在") from error
    except (psycopg.Error, DatabaseConfigurationError) as error:
        raise KnowledgeStoreUnavailableError("知识库暂时无法保存文档") from error


def stage_uploaded_document(result: dict, source_filename: str, source_pdf: bytes) -> None:
    """异常页、原 PDF 和切块结果进入待审核区；此时绝不计算向量。"""
    if result.get("status") != "partial":
        raise ValueError("只有未通过校验的文档需要待审核")
    if KNOWLEDGE_STORE_BACKEND == "postgres":
        try:
            POSTGRES_VECTOR_STORE.stage_document(result, source_filename, source_pdf)
        except (psycopg.errors.UniqueViolation, ValueError) as error:
            raise DuplicateDocumentError("文档编号已存在") from error
        except (psycopg.Error, DatabaseConfigurationError) as error:
            raise KnowledgeStoreUnavailableError("知识库暂时无法保存待审核文档") from error
        return
    with _REVIEW_LOCK:
        document_id = result["document_id"]
        if document_id in _PENDING_DOCUMENTS or any(
            record["chunk_id"].startswith(f"{document_id}-p") for record in VECTOR_STORE.records
        ):
            raise DuplicateDocumentError("文档编号已存在")
        _PENDING_DOCUMENTS[document_id] = {
            "result": result, "source_filename": source_filename,
            "source_pdf": source_pdf, "approved_pages": [], "review_events": [],
        }


def get_pending_document(document_id: str) -> dict:
    try:
        if KNOWLEDGE_STORE_BACKEND == "postgres":
            item = POSTGRES_VECTOR_STORE.get_staged_document(document_id)
            if item is None:
                raise ReviewDocumentNotFoundError("待审核文档不存在")
            return {"result": item["ingestion_result"],
                    "approved_pages": item["approved_pages"],
                    "source_pdf": item["source_pdf"],
                    "review_events": item["review_events"]}
        with _REVIEW_LOCK:
            item = _PENDING_DOCUMENTS.get(document_id)
            if item is None:
                raise ReviewDocumentNotFoundError("待审核文档不存在")
            return dict(item)
    except (psycopg.Error, DatabaseConfigurationError) as error:
        raise KnowledgeStoreUnavailableError("待审核文档暂不可读取") from error


def publish_pending_pages(document_id: str, pages: list[int], reviewer: str) -> dict:
    """仅管理员指定的有文本页面进入正式检索，其余继续隔离。"""
    if KNOWLEDGE_STORE_BACKEND == "postgres":
        try:
            return POSTGRES_VECTOR_STORE.publish_staged_pages(document_id, pages, reviewer)
        except LookupError as error:
            raise ReviewDocumentNotFoundError("待审核文档不存在") from error
        except ValueError as error:
            raise ReviewConflictError(str(error)) from error
        except (psycopg.Error, DatabaseConfigurationError) as error:
            raise KnowledgeStoreUnavailableError("知识库暂时无法发布审核片段") from error
    with _REVIEW_LOCK:
        item = _PENDING_DOCUMENTS.get(document_id)
        if item is None:
            raise ReviewDocumentNotFoundError("待审核文档不存在")
        result = item["result"]
        available = {page["page"] for page in result["pages"]}
        approved = set(item["approved_pages"])
        if not pages or len(set(pages)) != len(pages) or any(
            not isinstance(page, int) or isinstance(page, bool) or page < 1 for page in pages
        ) or not set(pages) <= available or set(pages) & approved:
            raise ReviewConflictError("批准页码无效或已发布")
        selected = [chunk for chunk in result["chunks"] if chunk["page"] in pages]
        if not selected or {chunk["page"] for chunk in selected} != set(pages):
            raise ReviewConflictError("所选页面没有可发布的文字片段")
        index_knowledge_chunks([{**chunk, "publication_status": "published",
                                 "validation_status": "approved"} for chunk in selected])
        item["approved_pages"] = sorted(approved | set(pages))
        item["review_events"].append({"reviewer": reviewer, "approved_pages": sorted(pages)})
        return {"document_id": document_id,
                "published_pages": item["approved_pages"],
                "remaining_pages": sorted(available - set(item["approved_pages"])),
                "published_chunk_count": len(selected)}


def search_knowledge_base(
    query: str, limit: int = 3, *, backend: str | None = None
) -> list[dict]:
    """检索后执行确定性证据门禁，供普通接口和离线模式使用。"""
    candidates = search_knowledge_candidates(query, limit, backend=backend)
    return filter_evidence_for_question(query, candidates)[:limit]


def search_knowledge_candidates(
    query: str, limit: int = 3, *, backend: str | None = None
) -> list[dict]:
    """返回相似度合格的宽召回候选，不执行答案类型门禁。

    Agent 真实模式会先做三态规则分类，再让结构化证据审查器复核不确定候选；
    普通知识库接口仍通过 ``search_knowledge_base`` 返回确定性过滤后的结果。
    """
    selected_backend = KNOWLEDGE_STORE_BACKEND if backend is None else backend
    if selected_backend not in {"memory", "postgres"}:
        raise ValueError("知识库检索后端只能是 memory 或 postgres")
    candidate_limit = limit * 3
    if selected_backend == "postgres":
        try:
            results = POSTGRES_VECTOR_STORE.search(query, candidate_limit)
        except (psycopg.Error, DatabaseConfigurationError) as error:
            raise KnowledgeStoreUnavailableError("知识库暂时无法检索文档") from error
        admitted = [
            result for result in results
            if result["score"] >= MIN_SEMANTIC_SIMILARITY
        ]
        return admitted

    published_records = [record for record in VECTOR_STORE.records
                         if record.get("publication_status", "published") == "published"]
    if published_records:
        try:
            hybrid_results = hybrid_search(
                query,
                published_records,
                VECTOR_STORE.embedding_service,
                candidate_limit,
                reranker=RERANKER,
                min_semantic_similarity=MIN_SEMANTIC_SIMILARITY,
            )
        except RerankerUnavailableError:
            # 重排是“提升质量”的可选阶段，短暂故障不应该让整个知识问答接口失败。
            LOGGER.warning("Reranker 暂不可用，本次检索降级为 RRF", exc_info=True)
            hybrid_results = hybrid_search(
                query,
                published_records,
                VECTOR_STORE.embedding_service,
                candidate_limit,
                min_semantic_similarity=MIN_SEMANTIC_SIMILARITY,
            )
        return [
            {
                **{
                    key: value
                    for key, value in result.items()
                    if key not in {"semantic_score", "keyword_score", "rrf_score", "rerank_score"}
                },
                # 有精排分数就代表最终排序由模型决定；否则使用融合分数。
                "score": result.get("rerank_score", result["rrf_score"]),
            }
            for result in hybrid_results
        ]
    keyword_results = keyword_search(query, KNOWLEDGE_CHUNKS, candidate_limit)
    return [
        {
            **{key: value for key, value in result.items() if key != "keyword_score"},
            "score": result["keyword_score"],
        }
        for result in keyword_results
    ]

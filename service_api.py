"""显式售后 API：知识证据与 Java 业务操作由用户直接选择。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt

from auth import authenticate_request
from evidence_review import ConfiguredEvidenceReviewer, EvidenceReviewReply, verify_evidence_review
from evidence_support import partition_evidence_for_question
from knowledge_base import KnowledgeStoreUnavailableError, search_knowledge_candidates
from knowledge_models import KnowledgeSearchRequest
from order_service_client import OrderServiceError
from return_draft_client import (
    JavaReturnDraftGateway, ReturnDraftExpired, ReturnDraftRejected,
    ReturnDraftResponse, ReturnOrderChanged,
)
from return_review_client import ReturnReviewRequest


class KnowledgeAnswerRequest(KnowledgeSearchRequest):
    review_mode: Literal["rules", "live"] = "rules"


class KnowledgeAnswerResponse(BaseModel):
    status: Literal["SUPPORTED", "INSUFFICIENT_EVIDENCE"]
    answer: str
    evidence: list[dict]
    missing_information: str = ""


class ReturnConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product: str = Field(min_length=1)
    amount_cents: StrictInt = Field(ge=0)
    expires_at: AwareDatetime


OrderQuery = Callable[[str], dict | None]


def build_service_router(
    *, order_query: OrderQuery, eligibility_query: OrderQuery,
    draft_gateway: JavaReturnDraftGateway,
    evidence_reviewer: ConfiguredEvidenceReviewer,
) -> APIRouter:
    router = APIRouter(prefix="/service", tags=["显式售后服务"],
                       dependencies=[Depends(authenticate_request)])

    @router.post("/knowledge/answer", response_model=KnowledgeAnswerResponse)
    def answer_knowledge(request: KnowledgeAnswerRequest) -> KnowledgeAnswerResponse:
        try:
            candidates = search_knowledge_candidates(request.query, request.limit)
        except KnowledgeStoreUnavailableError:
            raise HTTPException(status_code=503, detail="知识库暂时无法检索") from None
        partition = partition_evidence_for_question(request.query, candidates)
        evidence = partition["admitted"][:request.limit]
        missing = "知识库中没有足以回答该问题的证据。"
        if request.review_mode == "live":
            review_candidates = partition["admitted"] + partition["uncertain"]
            if review_candidates:
                try:
                    reviewed = evidence_reviewer(request.query, review_candidates)
                    raw = reviewed.verdict if isinstance(reviewed, EvidenceReviewReply) else reviewed
                    verdict, evidence = verify_evidence_review(
                        raw, review_candidates, limit=request.limit,
                    )
                    missing = verdict.missing_information or missing
                except (httpx.HTTPError, ValueError, RuntimeError):
                    # 模型或引文校验失败时停止回答，不降级为未复核的候选片段。
                    raise HTTPException(status_code=503, detail="证据复核暂不可用") from None
        if not evidence:
            return KnowledgeAnswerResponse(status="INSUFFICIENT_EVIDENCE", answer=missing,
                                           evidence=[], missing_information=missing)
        answer = "\n\n".join(
            f"{item['content']}\n[{item['title']} 第{item['page']}页]"
            for item in evidence
        )
        return KnowledgeAnswerResponse(status="SUPPORTED", answer=answer, evidence=evidence)

    def from_java(operation: Callable[[], dict]) -> dict:
        try:
            return operation()
        except ReturnDraftRejected as error:
            raise HTTPException(status_code=error.status_code, detail=error.code) from None
        except ReturnDraftExpired as error:
            raise HTTPException(status_code=410, detail=str(error)) from None
        except ReturnOrderChanged as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        except (OrderServiceError, httpx.RequestError):
            raise HTTPException(status_code=503, detail="订单服务暂不可用或业务操作失败") from None

    @router.get("/orders/{order_id}")
    def get_order(order_id: str = Path(pattern=r"^O-[0-9]{4}$")) -> dict:
        order = from_java(lambda: order_query(order_id))
        if order is None:
            raise HTTPException(status_code=404, detail="订单不存在")
        return order

    @router.get("/orders/{order_id}/return-eligibility")
    def get_eligibility(order_id: str = Path(pattern=r"^O-[0-9]{4}$")) -> dict:
        result = from_java(lambda: eligibility_query(order_id))
        if result is None:
            raise HTTPException(status_code=404, detail="订单不存在")
        return result

    @router.post("/orders/{order_id}/return-drafts")
    def start_draft(order_id: str = Path(pattern=r"^O-[0-9]{4}$")) -> dict:
        return from_java(lambda: draft_gateway.start(order_id))

    @router.get("/orders/{order_id}/return-drafts/{draft_id}")
    def get_draft(order_id: str = Path(pattern=r"^O-[0-9]{4}$"),
                  draft_id: UUID = Path()) -> dict:
        return from_java(lambda: draft_gateway.get(order_id, str(draft_id)))

    @router.post("/orders/{order_id}/return-drafts/{draft_id}/reason")
    def submit_reason(request: ReturnReviewRequest,
                      order_id: str = Path(pattern=r"^O-[0-9]{4}$"),
                      draft_id: UUID = Path()) -> dict:
        return from_java(lambda: draft_gateway.submit(
            order_id, str(draft_id), request.return_reason, request.reason_code))

    @router.post("/orders/{order_id}/return-drafts/{draft_id}/confirm")
    def confirm_draft(request: ReturnConfirmationRequest,
                      order_id: str = Path(pattern=r"^O-[0-9]{4}$"),
                      draft_id: UUID = Path()) -> dict:
        snapshot = ReturnDraftResponse.model_validate(
            from_java(lambda: draft_gateway.get(order_id, str(draft_id))))
        if (snapshot.product != request.product
                or snapshot.amount_cents != request.amount_cents
                or snapshot.expires_at != request.expires_at):
            raise HTTPException(status_code=409, detail="确认快照已变化，请重新读取并核对")
        # Java 仍在事务中重新检查权威订单和草稿；此处只绑定用户看到的快照。
        return from_java(lambda: draft_gateway.confirm(order_id, str(draft_id)))

    @router.post("/orders/{order_id}/return-drafts/{draft_id}/cancel")
    def cancel_draft(order_id: str = Path(pattern=r"^O-[0-9]{4}$"),
                     draft_id: UUID = Path()) -> dict:
        return from_java(lambda: draft_gateway.cancel(order_id, str(draft_id)))

    @router.post("/orders/{order_id}/return-drafts/{draft_id}/refresh")
    def refresh_draft(order_id: str = Path(pattern=r"^O-[0-9]{4}$"),
                      draft_id: UUID = Path()) -> dict:
        return from_java(lambda: draft_gateway.refresh(order_id, str(draft_id)))

    return router

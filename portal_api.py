"""登录后的会话/图片问答入口，复用现有受控业务图，不重放历史批准。"""

from datetime import datetime, timezone
import re
from typing import Callable, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from agent_graph import (AgentGraphStartRequest, AgentGraphResumeRequest, ReturnReasonRequest,
                         AgentGraphInputError, AgentGraphConfigurationError)
from auth import AuthenticatedUser, authenticate_request
from evidence_review import (ConfiguredEvidenceReviewer, EvidenceReviewReply, verify_evidence_review,
                             EvidenceResponseFormatError,EvidenceContractError)
from evidence_support import partition_evidence_for_question
from knowledge_base import search_knowledge_candidates
from model_rate_limit import ModelAdmissionError
from preview_tool_call import ModelAPIError
from return_draft_client import ReturnOrderChanged
from portal_store import PortalConflictError, portal_store
from vision_service import ConfiguredVisionGateway, MAX_IMAGE_BYTES, validate_image
from model_runtime import model_provider, public_runtime
from ollama_gateway import LocalModelFormatError
from order_service_client import OrderServiceError
from model_context import ContextBudgetExceeded
from conversation_context import build_conversation_history
from conversation_memory import memory_context,ConversationMemoryUnavailable


class NewConversation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(default="新会话", min_length=1, max_length=100)
    conversation_id: UUID | None = None


class TurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: UUID
    message: str = Field(min_length=1, max_length=1000)
    mode: Literal["mock", "live"] = "live"
    image_ids: list[UUID] = Field(default_factory=list, max_length=3)
    reuse_images: bool = False
    use_policy: bool = False
    policy_query: str | None = Field(default=None, min_length=1, max_length=200)
    allow_external_images: bool = False


class TurnAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["confirm", "cancel", "reason", "refresh"]
    expected_draft_id: str | None = Field(default=None, max_length=100)
    return_reason: str | None = Field(default=None, min_length=1, max_length=1000)
    reason_code: Literal["OTHER", "QUALITY_ISSUE", "PERSONAL_PREFERENCE"] = "OTHER"


def visible_history(turns: list[dict], question: str = "") -> list[dict]:
    return build_conversation_history(turns, question)


def resolve_followup(question: str, turns: list[dict]) -> str:
    """只延续用户明确提供过的订单编号，不继承旧资格、金额、工具或批准状态。"""
    if re.search(r"O-\d{4}", question):
        return question
    if re.search(r"(?:订单(?:号|编号)?|退货|退款)\s*(?:是|为|[：:])?\s*[A-Za-z0-9_-]*[0-9]",question):
        return question  # 用户已明确给出编号，即使格式不完整，也不能拿旧订单替换。
    if not any(word in question for word in ("这", "那", "它", "继续", "申请", "退货")):
        return question
    for turn in reversed(turns):
        ids = set(re.findall(r"(?<![A-Za-z0-9_-])O-\d{4}(?![A-Za-z0-9_-])", turn["message"]))
        if len(ids) > 1:
            return question  # 多订单不能随意选一个，交给当前问题澄清。
        if ids:
            last = turns[-1] if turns else {}
            return_context = ("退货" in last.get("message", "") or
                              (last.get("result") or {}).get("tool_name") == "check_return_eligibility")
            if return_context and re.fullmatch(r"(?:那|那么|好|好的)?(?:就)?(?:帮我)?(?:申请|办理)(?:吧|一下)?[。！!]?", question):
                # 明确的“那帮我申请”在退货上下文中只扩展为新草稿，绝不继承原确认。
                question = "帮我申请退货"
            return question + f"（本轮指代订单 {next(iter(ids))}，当前状态和资格须重新查询）"
    return question


def reviewed_policy(question: str, mode: str) -> dict:
    candidates = search_knowledge_candidates(question, 3)
    partition = partition_evidence_for_question(question, candidates)
    evidence = partition["admitted"][:3]
    if mode == "live" and (partition["admitted"] or partition["uncertain"]):
        review_candidates = partition["admitted"] + partition["uncertain"]
        reply = ConfiguredEvidenceReviewer()(question, review_candidates)
        raw = reply.verdict if isinstance(reply, EvidenceReviewReply) else reply
        _, evidence = verify_evidence_review(raw, review_candidates, limit=3)
    return {"status": "SUPPORTED" if evidence else "INSUFFICIENT_EVIDENCE", "evidence": evidence,
            "answer": "\n\n".join(f"{item['content']}\n[{item['title']} 第{item['page']}页]"
                                   for item in evidence) or "没有足够的已发布政策证据，无法确认适用规则。"}


def build_portal_router(*, start_run: Callable, read_run: Callable, resume_run: Callable,
                        submit_reason: Callable, refresh_run: Callable,
                        vision_gateway=None, policy_reader=reviewed_policy) -> APIRouter:
    router = APIRouter(prefix="/conversations", tags=["历史会话与图片问答"])
    vision = vision_gateway or ConfiguredVisionGateway()

    @router.get("/runtime")
    def runtime(user: AuthenticatedUser = Depends(authenticate_request)):
        return public_runtime()

    @router.get("")
    def list_conversations(offset: int = Query(default=0, ge=0, le=10000),
                           user: AuthenticatedUser = Depends(authenticate_request)):
        items = portal_store().list_conversations(user.user_id, offset)
        return {"items": items, "next_offset": offset+50 if len(items) == 50 else None}

    @router.post("")
    def create_conversation(body: NewConversation, user: AuthenticatedUser = Depends(authenticate_request)):
        return portal_store().create_conversation(
            user.user_id, body.title, str(body.conversation_id) if body.conversation_id else None)

    @router.get("/{conversation_id}")
    def conversation(conversation_id: UUID, before: UUID | None = None,
                     user: AuthenticatedUser = Depends(authenticate_request)):
        store=portal_store()
        return store.conversation(user.user_id,str(conversation_id),str(before)) if before else store.conversation(user.user_id,str(conversation_id))

    @router.get("/{conversation_id}/requests/{request_id}")
    def request_turn(conversation_id: UUID, request_id: UUID,
                     user: AuthenticatedUser = Depends(authenticate_request)):
        return portal_store().request_turn(user.user_id,str(conversation_id),str(request_id))

    @router.get("/{conversation_id}/turns/{turn_id}")
    def source_turn(conversation_id: UUID,turn_id: UUID,user: AuthenticatedUser=Depends(authenticate_request)):
        return portal_store().turn(user.user_id,str(conversation_id),str(turn_id))

    @router.post("/{conversation_id}/images")
    async def upload_image(conversation_id: UUID, file: UploadFile = File(),
                           user: AuthenticatedUser = Depends(authenticate_request)):
        store = portal_store()
        await run_in_threadpool(store.conversation, user.user_id, str(conversation_id))
        try:
            content = await file.read(MAX_IMAGE_BYTES+1)
        finally:
            await file.close()
        try:
            image = await run_in_threadpool(validate_image, content)
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        return await run_in_threadpool(store.add_image, user.user_id, str(conversation_id), image)

    @router.get("/{conversation_id}/images/{image_id}")
    def get_image(conversation_id: UUID, image_id: UUID,
                  user: AuthenticatedUser = Depends(authenticate_request)):
        image = portal_store().image(user.user_id, str(conversation_id), str(image_id))
        return Response(image["content"], media_type=image["mime_type"], headers={
            "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
            "Cross-Origin-Resource-Policy": "same-origin"})

    @router.post("/{conversation_id}/turns")
    def send_turn(conversation_id: UUID, body: TurnRequest,
                  user: AuthenticatedUser = Depends(authenticate_request)):
        store = portal_store()
        cid = str(conversation_id)
        previous = store.conversation(user.user_id, cid)["turns"]
        image_ids = [str(item) for item in body.image_ids]
        if len(set(image_ids)) != len(image_ids):
            raise HTTPException(400, "同一图片不能重复选择")
        if not image_ids and body.reuse_images:
            image_ids = next(([str(item) for item in turn["image_ids"]]
                              for turn in reversed(previous) if turn["image_ids"]), [])
            if not image_ids:
                raise HTTPException(400, "本会话没有可沿用的图片，请先上传图片或取消沿用选项")
        if (image_ids and body.mode == "live" and model_provider() != "ollama"
                and not body.allow_external_images):
            raise HTTPException(400, "须明确同意将所选图片、问题及最近对话发送到智谱后才能调用视觉模型")
        images = [store.image(user.user_id, cid, image_id) for image_id in image_ids]
        kind = "vision" if images else "agent"
        question = resolve_followup(body.message, previous)
        if not images and body.use_policy:
            question += "\n请同时结合售后政策：" + (body.policy_query or body.message[:200])
        if len(question) > 1000:
            raise HTTPException(400, "问题过长，请缩短问题并明确提供订单编号；未截断问题或执行任务")
        turn, created = store.claim_turn(user.user_id, cid, body.model_dump(mode="json"), image_ids, kind)
        if not created:
            return turn  # 请求编号重复时只返回原状态；不重试模型或业务操作。
        tid = str(turn["id"])
        try:
            candidates=memory_context(store,user.user_id,cid,body.message,previous) if body.mode=='live' else previous
            history = visible_history(candidates, body.message)
            if images:
                result = vision(body.message, images, history, body.mode)
                need_policy = body.use_policy or (body.mode == "live" and any(
                    word in body.message for word in ("政策","退货","退款","保修","赔偿","运费")))
                policy = policy_reader(body.policy_query or body.message[:200], body.mode) if need_policy else None
                answer = ("图片观察（模型推断，需核对）：\n" + result["answer"]
                          + "\n\n不确定与待核实：" + result["uncertainty"])
                if policy:
                    answer += "\n\n政策原文与引用（是否适用于该订单仍需业务核实）：\n" + policy["answer"]
                answer += "\n\n图片问答不会批准或提交退款；办理申请须另行查询订单、校验资格并明确确认。"
                result = {**result, "answer": answer, "kind": "vision", "status": "COMPLETED",
                          "policy": policy, "mode": body.mode, "business_write_executed": False}
            else:
                # 不允许把历史正文写进公开请求模型；历史仅由仓库读取并作为内部参数传递。
                request = AgentGraphStartRequest(thread_id=tid, message=question, mode=body.mode)
                result = start_run(request, user, history).model_dump(mode="json")
            return store.finish_turn(user.user_id, cid, tid, result, result["status"])
        except Exception as error:
            # 图调用可能已写入，标未知并保留同一任务ID；视觉路径无业务执行器，可标失败。
            known_input = isinstance(error, (AgentGraphInputError, AgentGraphConfigurationError))
            known_model_failure = isinstance(error,(ModelAPIError,ModelAdmissionError,LocalModelFormatError,
                                                    EvidenceResponseFormatError,EvidenceContractError,httpx.RequestError))
            known_order_failure=isinstance(error,OrderServiceError)
            known_context_failure=isinstance(error,(ContextBudgetExceeded,ConversationMemoryUnavailable))
            explanation = ("模拟模式需要明确的订单或政策问题，请调整问题。" if isinstance(error, AgentGraphInputError)
                           else "真实模型配置不可用，请选择模拟模式或检查服务端配置。")
            failure_result = {"answer": explanation, "status": "FAILED", "mode": body.mode} if known_input else None
            if known_model_failure:
                failure_result = {"answer":"模型未返回有效结果，请稍后重新发送；本轮没有提交售后申请。",
                                  "status":"FAILED","mode":body.mode}
            if known_order_failure:
                failure_result={"answer":"订单服务暂时不可用，请稍后再查询；不能据此判断订单不存在。本轮没有提交售后申请。",
                                "status":"FAILED","mode":body.mode}
            if known_context_failure:
                failure_result={"answer":str(error),"status":"FAILED","mode":body.mode}
            store.finish_turn(user.user_id, cid, tid, failure_result,
                              "FAILED" if images or known_input or known_model_failure or known_order_failure or known_context_failure else "RESULT_UNKNOWN", type(error).__name__)
            if known_context_failure:
                raise HTTPException(503 if isinstance(error,ConversationMemoryUnavailable) else 422,str(error)) from None
            if known_input:
                raise HTTPException(400 if isinstance(error, AgentGraphInputError) else 503, explanation) from None
            if isinstance(error, (HTTPException, ModelAdmissionError)):
                raise
            if known_order_failure:
                raise HTTPException(503,"订单服务暂时不可用，请稍后查询；这不代表订单不存在") from None
            if known_model_failure:
                raise HTTPException(503,"模型未准备好、繁忙或输出格式无效；请稍后再试，未切换供应商") from None
            if images:
                detail = ("视觉模型暂时限流，本轮未取得识图结果；未执行业务写入，未切换模型。"
                          if isinstance(error, ModelAPIError) and error.status_code == 429 else
                          "视觉服务未启用或未返回有效结果；本轮未执行业务写入，请核对服务端配置。")
                raise HTTPException(503, detail) from None
            raise HTTPException(503, "本轮未取得完整结果，请同步原任务；页面不会自动重发") from None

    @router.post("/{conversation_id}/turns/{turn_id}/actions")
    def act(conversation_id: UUID, turn_id: UUID, body: TurnAction,
            user: AuthenticatedUser = Depends(authenticate_request)):
        store = portal_store()
        cid, tid = str(conversation_id), str(turn_id)
        turn = store.turn(user.user_id, cid, tid)
        if turn["kind"] != "agent":
            raise HTTPException(409, "图片问答没有业务确认权限")
        if body.action == "reason" and not body.return_reason:
            raise HTTPException(400, "请填写退货原因")
        store.claim_action(user.user_id, cid, tid)
        # 所有门户操作先取得执行权，再读取和核对最新快照，避免并发刷新
        # 恰好在校验与占用之间换掉草稿，让旧批准被用于新草稿。
        state = None
        try:
            turn = store.turn(user.user_id, cid, tid)
            state = read_run(tid, user).model_dump(mode="json")
            if body.action == "confirm" and state.get("return_draft"):
                if body.expected_draft_id != state["return_draft"]["draft_id"]:
                    raise HTTPException(409, "确认快照已变化，请先同步并重新核对")
        except Exception:
            # 此处还未调用任何执行节点；释放占用并保留权威状态，不能误标未知写入。
            saved = state if state is not None else turn.get("result")
            status = state["status"] if state is not None else turn["status"]
            store.finish_turn(user.user_id, cid, tid, saved, status)
            raise
        try:
            if body.action in {"confirm", "cancel"}:
                result = resume_run(tid, AgentGraphResumeRequest(approved=body.action == "confirm"), user)
            elif body.action == "reason":
                result = submit_reason(tid, ReturnReasonRequest(
                    return_reason=body.return_reason, reason_code=body.reason_code), user)
            else:
                result = refresh_run(tid, user)
            raw = result.model_dump(mode="json")
            return store.finish_turn(user.user_id, cid, tid, raw, raw["status"])
        except Exception as error:
            if isinstance(error, ReturnOrderChanged):
                # 已知的旧确认失效不是未知写入；读取已保存状态，不重放执行节点。
                state = read_run(tid, user).model_dump(mode="json")
                store.finish_turn(user.user_id, cid, tid, state, state["status"])
                raise HTTPException(409, "确认快照已失效，请同步并刷新后重新确认") from None
            store.finish_turn(user.user_id, cid, tid, turn.get("result"), "RESULT_UNKNOWN", type(error).__name__)
            if isinstance(error, HTTPException):
                raise
            raise HTTPException(503, "操作结果未知，请同步同一任务，不能重新创建申请") from None

    @router.post("/{conversation_id}/turns/{turn_id}/sync")
    def sync(conversation_id: UUID, turn_id: UUID, user: AuthenticatedUser = Depends(authenticate_request)):
        store = portal_store()
        cid, tid = str(conversation_id), str(turn_id)
        conversation = store.conversation(user.user_id, cid)
        turn = store.turn(user.user_id, cid, tid)
        if turn["kind"] != "agent":
            return store.recover_interrupted_vision(user.user_id, cid, tid)
        if conversation["active_turn_id"]:
            age = (datetime.now(timezone.utc)-turn["updated_at"]).total_seconds()
            if age < 900:
                raise HTTPException(409, "请求可能仍在执行；请等待，不重放工具")
        state = read_run(tid, user).model_dump(mode="json")
        if state["status"] == "RUNNING":
            raise HTTPException(409, "检查点仍在执行中，需核实原业务状态；未重新执行")
        return store.finish_turn(user.user_id, cid, tid, state, state["status"],
                                 expected_snapshot=(turn["updated_at"], conversation["active_turn_id"]))

    return router

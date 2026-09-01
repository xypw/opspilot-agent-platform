"""OpsPilot API：文档入库、Agent 聊天、查询与高风险操作确认。"""

from io import BytesIO

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import ValidationError

from chat_models import ChatRequest, ChatResponse
from chat_service import ChatConfigurationError, MockInputError, chat, run_mock_chat
from action_models import PendingAction
from checkpoint_models import AgentRunCheckpoint, AgentRunStartRequest
from checkpoint_store import (
    RUN_STORE,
    IdempotencyConflictError,
    RunNotFoundError,
    RunStateError,
)
from document_ingestion import ingest_pdf
from document_models import DocumentIngestionResponse
from document_parser import PDFNeedsOCRError
from knowledge_base import index_knowledge_chunks, search_knowledge_base
from knowledge_models import KnowledgeSearchRequest, KnowledgeSearchResult
from orders import query_order
from pending_actions import ACTION_STORE, ActionNotFoundError, TicketNotFoundError
from preview_tool_call import ModelAPIError
from tickets import query_ticket
from tool_args import RequestPriorityChangeArgs

app = FastAPI(
    title="OpsPilot 企业知识库与智能工单 Agent",
    description="POST /chat 支持查询及创建优先级修改申请；写操作必须另行确认。默认 mock，live 才请求真实模型。仅供本机教学。",
    version="0.1.0",
)

MAX_PDF_BYTES = 10 * 1024 * 1024


@app.get("/health", summary="检查服务是否启动")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "opspilot"}


@app.post(
    "/documents/upload",
    response_model=DocumentIngestionResponse,
    summary="上传、解析并切分 PDF 文档",
    responses={
        400: {"description": "PDF 无法读取或格式无效"},
        413: {"description": "文件超过 10 MB"},
        415: {"description": "当前只支持 PDF"},
        422: {"description": "字段无效或整份 PDF 需要 OCR"},
    },
)
async def upload_document(
    file: UploadFile = File(description="不超过 10 MB 的 PDF 文件"),
    document_id: str = Form(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$"),
    title: str = Form(min_length=1, max_length=200),
) -> dict:
    filename = file.filename or ""
    if file.content_type != "application/pdf" or not filename.lower().endswith(".pdf"):
        await file.close()
        raise HTTPException(status_code=415, detail="当前只支持 PDF 文件")

    try:
        content = await file.read(MAX_PDF_BYTES + 1)
    finally:
        await file.close()
    if len(content) > MAX_PDF_BYTES:
        raise HTTPException(status_code=413, detail="PDF 文件不能超过 10 MB")

    try:
        result = ingest_pdf(BytesIO(content), document_id=document_id, title=title)
        index_knowledge_chunks(result["chunks"])
        return result
    except PDFNeedsOCRError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    except ValueError:
        raise HTTPException(status_code=400, detail="PDF 文件无法读取或格式无效") from None


@app.get(
    "/tickets/{ticket_id}",
    summary="按编号查询模拟工单",
    responses={404: {"description": "工单不存在"}},
)
def get_ticket(ticket_id: str) -> dict[str, str]:
    # 接口层负责 HTTP；查询逻辑单独放在 tickets.py，方便以后给 Agent 复用。
    ticket = query_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="工单不存在")
    return ticket


@app.get(
    "/orders/{order_id}",
    summary="按编号查询模拟订单",
    responses={404: {"description": "订单不存在"}},
)
def get_order(order_id: str) -> dict[str, str]:
    order = query_order(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="订单不存在")
    return order


@app.post(
    "/knowledge/search",
    response_model=list[KnowledgeSearchResult],
    summary="搜索企业知识库并返回可引用片段",
)
def search_knowledge(request: KnowledgeSearchRequest) -> list[dict]:
    results = search_knowledge_base(request.query, request.limit)
    return results


@app.post(
    "/actions/priority-changes",
    response_model=PendingAction,
    summary="创建待确认的工单优先级修改",
    responses={404: {"description": "工单不存在"}},
)
def propose_priority_change(request: RequestPriorityChangeArgs) -> PendingAction:
    try:
        return ACTION_STORE.propose_priority_change(request.ticket_id, request.new_priority)
    except TicketNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from None


@app.post(
    "/actions/{action_id}/confirm",
    response_model=PendingAction,
    summary="确认并执行一次待确认操作",
    responses={404: {"description": "操作或工单不存在"}},
)
def confirm_action(action_id: str) -> PendingAction:
    try:
        return ACTION_STORE.confirm(action_id)
    except (ActionNotFoundError, TicketNotFoundError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from None


@app.post(
    "/agent/runs",
    response_model=AgentRunCheckpoint,
    summary="启动可恢复的模拟 Agent 运行",
    responses={
        400: {"description": "模拟输入无法生成有效工具调用"},
        409: {"description": "幂等键已用于不同消息"},
    },
)
def start_agent_run(request: AgentRunStartRequest) -> AgentRunCheckpoint:
    try:
        return RUN_STORE.start(
            request.thread_id,
            request.idempotency_key,
            request.message,
            run_mock_chat,
        )
    except MockInputError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None
    except IdempotencyConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None


@app.get(
    "/agent/runs/{run_id}",
    response_model=AgentRunCheckpoint,
    summary="读取 Agent Checkpoint",
    responses={404: {"description": "Agent 运行不存在"}},
)
def get_agent_run(run_id: str) -> AgentRunCheckpoint:
    try:
        return RUN_STORE.get(run_id)
    except RunNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from None


@app.post(
    "/agent/runs/{run_id}/confirm",
    response_model=AgentRunCheckpoint,
    summary="确认并恢复暂停的 Agent 运行",
    responses={
        404: {"description": "Agent 运行或待确认操作不存在"},
        409: {"description": "当前状态不能确认"},
    },
)
def confirm_agent_run(run_id: str) -> AgentRunCheckpoint:
    try:
        return RUN_STORE.confirm(run_id)
    except (RunNotFoundError, ActionNotFoundError, TicketNotFoundError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from None
    except RunStateError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None


@app.post(
    "/chat",
    response_model=ChatResponse,
    summary="选择查询或待确认操作工具（默认模拟，不消耗模型额度）",
    responses={
        400: {"description": "模拟问题缺少唯一编号，或修改参数不完整"},
        502: {"description": "模型请求失败或返回无效数据"},
        503: {"description": "真实模式配置不可用，或上游限流/不可用"},
        504: {"description": "模型请求超时"},
    },
)
def post_chat(request: ChatRequest) -> dict:
    # 这里用同步 def，匹配现有同步 HTTP 客户端；FastAPI 在线程池运行该路由。
    try:
        return chat(request.message, request.mode)
    except MockInputError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None
    except ChatConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from None
    except ModelAPIError as error:
        if error.status_code == 429:
            raise HTTPException(status_code=503, detail="模型繁忙或达到使用限制；未切换模拟结果，请稍后再试。") from None
        if error.status_code in (401, 403):
            raise HTTPException(status_code=503, detail="模型凭据或权限配置不可用，请检查本地配置。") from None
        raise HTTPException(status_code=502, detail="上游模型请求失败，本次流程未完成。") from None
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="模型请求超时，本次流程未完成。") from None
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="无法完成模型网络请求，本次流程未完成。") from None
    except (ValidationError, ValueError):
        # 不回显原始模型响应或校验输入，防止错误响应意外泄露数据。
        raise HTTPException(status_code=502, detail="模型返回的工具调用、参数或回答不符合约定。") from None

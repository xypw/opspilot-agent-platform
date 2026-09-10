"""OpsPilot API：文档入库、Agent 聊天、查询与高风险操作确认。"""

from io import BytesIO

import httpx
import tool_executor
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from return_draft_client import ReturnOrderChanged
from pydantic import ValidationError

from chat_models import ChatRequest, ChatResponse
from chat_service import ChatConfigurationError, MockInputError, chat, run_mock_chat
from agent_graph import (
    ReturnReasonRequest,
    submit_return_reason,
    AgentGraphConfigurationError,
    AgentGraphInputError,
    AgentGraphNotFoundError,
    AgentGraphResumeRequest,
    AgentGraphResponse,
    AgentGraphStartRequest,
    AgentGraphStateError,
    ConfiguredAgentModelGateway,
    build_agent_graph,
    get_agent_graph_state,
    refresh_return_confirmation,
    resume_agent_graph,
    start_agent_graph,
)
from action_models import PendingAction
from checkpoint_models import AgentRunCheckpoint, AgentRunStartRequest
from checkpoint_store import (
    IdempotencyConflictError,
    RunNotFoundError,
    RunStateError,
)
from document_ingestion import ingest_pdf
from document_models import DocumentIngestionResponse
from document_parser import PDFNeedsOCRError
from knowledge_base import index_knowledge_chunks, search_knowledge_base
from knowledge_models import KnowledgeSearchRequest, KnowledgeSearchResult
from langgraph_checkpointer import build_agent_checkpointer
from order_query_factory import build_order_query, build_return_eligibility_query
from order_service_client import OrderServiceError
from return_draft_client import JavaReturnDraftGateway
from order_query_factory import load_order_service_url
from orders import query_order
from pending_actions import (
    ActionNotFoundError,
    ActionStateError,
    TicketNotFoundError,
)
from preview_tool_call import ModelAPIError
from priority_change_graph import (
    PriorityChangeResumeRequest,
    PriorityChangeStartRequest,
    PriorityChangeWorkflowResponse,
    WorkflowNotFoundError,
    WorkflowStateError,
    build_priority_change_graph,
    resume_priority_change_workflow,
    start_priority_change_workflow,
)
from runtime_store_factory import build_runtime_stores
from tool_args import RequestPriorityChangeArgs

app = FastAPI(
    title="OpsPilot 企业知识库与智能工单 Agent",
    description="POST /chat 支持查询及创建优先级修改申请；写操作必须另行确认。默认 mock，live 才请求真实模型。仅供本机教学。",
    version="0.1.0",
)

MAX_PDF_BYTES = 10 * 1024 * 1024

@app.exception_handler(ReturnOrderChanged)
async def handle_return_order_changed(request, error: ReturnOrderChanged):
    # 同时覆盖启动、补充原因和确认入口，不把业务冲突误报为服务不可用。
    return JSONResponse(status_code=409, content={
        "detail": {"code": "ORDER_CHANGED", "message": str(error)},
    })

# 运行时只创建一次 Store，并让 FastAPI 路由和 Tool Executor 共享同一实例。
# REDIS_URL 已配置但 Redis 不可用时 build_runtime_stores 会抛错，阻止“假持久化”启动。
_runtime_stores = build_runtime_stores()
ACTION_STORE = _runtime_stores.action_store
RUN_STORE = _runtime_stores.run_store
TICKET_REPOSITORY = _runtime_stores.ticket_repository
tool_executor.ACTION_STORE = ACTION_STORE
tool_executor.TICKET_REPOSITORY = TICKET_REPOSITORY
# memory 保留工具分发器的离线默认实现；java 显式注入真实 HTTP 查询。
_configured_order_query = build_order_query()
if _configured_order_query is not None:
    tool_executor.ORDER_QUERY = _configured_order_query
# 退货日期和资格规则只在 Java 中实现；Python 只注入受控 HTTP 调用。
tool_executor.RETURN_ELIGIBILITY_QUERY = build_return_eligibility_query()

# LangGraph 需要一个长期存在的编译图和 Checkpointer，不能在每个 HTTP 请求中重新创建。
# 本课先使用 InMemorySaver；后续接入持久化 Checkpointer 后，即使服务重启也能恢复。
PRIORITY_CHANGE_GRAPH = build_priority_change_graph(ACTION_STORE)
# Agent 图使用独立 Checkpointer；避免与旧教学图共用同一个 thread_id 命名空间。
AGENT_CHECKPOINTER = build_agent_checkpointer()
RETURN_DRAFT_GATEWAY = JavaReturnDraftGateway(load_order_service_url())
AGENT_GRAPH = build_agent_graph(
    ACTION_STORE,
    ConfiguredAgentModelGateway(),
    checkpointer=AGENT_CHECKPOINTER,
    draft_gateway=RETURN_DRAFT_GATEWAY,
)


def query_ticket(ticket_id: str) -> dict[str, str] | None:
    """FastAPI 的稳定查询入口；底层仓库由启动配置决定。"""
    return TICKET_REPOSITORY.get_by_id(ticket_id)


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
def get_order(order_id: str) -> dict[str, object]:
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
    responses={
        404: {"description": "操作或工单不存在"},
        409: {"description": "待确认操作已取消"},
    },
)
def confirm_action(action_id: str) -> PendingAction:
    try:
        return ACTION_STORE.confirm(action_id)
    except (ActionNotFoundError, TicketNotFoundError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from None
    except ActionStateError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None


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
    except (RunStateError, ActionStateError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from None


@app.post(
    "/agent/runs/{run_id}/cancel",
    response_model=AgentRunCheckpoint,
    summary="取消等待用户确认的 Agent 运行",
    responses={
        404: {"description": "Agent 运行或待确认操作不存在"},
        409: {"description": "当前状态不能取消"},
    },
)
def cancel_agent_run(run_id: str) -> AgentRunCheckpoint:
    try:
        return RUN_STORE.cancel(run_id)
    except (RunNotFoundError, ActionNotFoundError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from None
    except (RunStateError, ActionStateError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from None


@app.post(
    "/workflows/priority-changes",
    response_model=PriorityChangeWorkflowResponse,
    summary="启动 LangGraph 工单优先级修改工作流",
    responses={404: {"description": "工单不存在"}},
)
def start_priority_change_graph(
    request: PriorityChangeStartRequest,
) -> PriorityChangeWorkflowResponse:
    """FastAPI 负责 HTTP；LangGraph 负责把流程运行到 interrupt。"""
    try:
        return start_priority_change_workflow(PRIORITY_CHANGE_GRAPH, request)
    except TicketNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from None


@app.post(
    "/workflows/priority-changes/{thread_id}/resume",
    response_model=PriorityChangeWorkflowResponse,
    summary="确认或拒绝并恢复 LangGraph 工作流",
    responses={409: {"description": "工作流不能恢复或操作状态冲突"}},
)
def resume_priority_change_graph(
    thread_id: str,
    request: PriorityChangeResumeRequest,
) -> PriorityChangeWorkflowResponse:
    """同一个 thread_id 让 LangGraph 找到之前暂停的 Checkpoint。"""
    try:
        return resume_priority_change_workflow(
            PRIORITY_CHANGE_GRAPH,
            thread_id,
            request,
        )
    except WorkflowNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from None
    except (ActionStateError, WorkflowStateError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from None


@app.post(
    "/agent-graph/runs",
    response_model=AgentGraphResponse,
    summary="启动 LangGraph Tool Calling Agent",
    responses={
        400: {"description": "模拟输入或模型工具调用不符合约定"},
        503: {"description": "真实模型配置不可用"},
    },
)
def start_langgraph_agent(request: AgentGraphStartRequest) -> AgentGraphResponse:
    """模型只选择工具；LangGraph 根据工具类型执行查询或暂停等待确认。"""
    try:
        return start_agent_graph(AGENT_GRAPH, request)
    except AgentGraphStateError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    except AgentGraphInputError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None
    except AgentGraphConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from None
    except OrderServiceError as error:
        raise HTTPException(status_code=503, detail=str(error)) from None
    except TicketNotFoundError as error:
        # 工具参数合法但目标工单不存在，属于业务资源不存在而不是模型服务故障。
        raise HTTPException(status_code=404, detail=str(error)) from None
    except (ValidationError, ValueError):
        # 模型输出的 JSON 或工具参数不符合契约时，不向客户端泄露原始模型内容。
        raise HTTPException(status_code=502, detail="模型返回的工具调用或参数不符合约定。") from None


@app.get(
    "/agent-graph/runs/{thread_id}",
    response_model=AgentGraphResponse,
    summary="读取 LangGraph Agent 当前状态",
    responses={404: {"description": "Agent 工作流不存在"}},
)
def get_langgraph_agent(thread_id: str) -> AgentGraphResponse:
    """页面刷新后无需重跑模型，可按 thread_id 读取当前 Checkpoint。"""
    try:
        return get_agent_graph_state(AGENT_GRAPH, thread_id, RETURN_DRAFT_GATEWAY)
    except AgentGraphNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from None
    except OrderServiceError as error:
        raise HTTPException(status_code=503, detail=str(error)) from None


@app.post("/agent-graph/runs/{thread_id}/return-reason", response_model=AgentGraphResponse,
          summary="补充退货原因并恢复同一会话")
def post_return_reason(thread_id: str, request: ReturnReasonRequest) -> AgentGraphResponse:
    try:
        return submit_return_reason(AGENT_GRAPH, thread_id, request)
    except AgentGraphNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from None
    except AgentGraphStateError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    except (OrderServiceError, AgentGraphConfigurationError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from None


@app.post(
    "/agent-graph/runs/{thread_id}/resume",
    response_model=AgentGraphResponse,
    summary="确认或拒绝并恢复 LangGraph Agent",
    responses={
        404: {"description": "Agent 工作流不存在"},
        409: {"description": "Agent 工作流当前不能恢复"},
    },
)
def resume_langgraph_agent(
    thread_id: str,
    request: AgentGraphResumeRequest,
) -> AgentGraphResponse:
    try:
        response = resume_agent_graph(AGENT_GRAPH, thread_id, request)
        if response.status == "STALE_CONFIRMATION":
            # 图已先保存失效状态，再由统一异常处理器向当前 HTTP 请求返回409。
            raise ReturnOrderChanged()
        return response
    except AgentGraphNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from None
    except AgentGraphStateError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    except TicketNotFoundError as error:
        # 等待确认期间目标工单可能被其他系统删除，因此恢复时也需要处理 404。
        raise HTTPException(status_code=404, detail=str(error)) from None
    except ActionStateError as error:
        # 底层操作已取消或已执行时，不能再违反状态机规则。
        raise HTTPException(status_code=409, detail=str(error)) from None
    except OrderServiceError as error:
        # 确认退货时 Java 业务服务不可用，保留 Checkpoint 供相同请求安全重试。
        raise HTTPException(status_code=503, detail=str(error)) from None


@app.post(
    "/agent-graph/runs/{thread_id}/refresh-return-confirmation",
    response_model=AgentGraphResponse,
    summary="刷新已失效的退货确认内容",
    responses={
        404: {"description": "Agent 工作流不存在"},
        409: {"description": "当前工作流不能刷新"},
        503: {"description": "Java 订单服务不可用"},
    },
)
def refresh_langgraph_return_confirmation(thread_id: str) -> AgentGraphResponse:
    try:
        return refresh_return_confirmation(AGENT_GRAPH, thread_id, RETURN_DRAFT_GATEWAY)
    except AgentGraphNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from None
    except AgentGraphStateError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    except OrderServiceError as error:
        raise HTTPException(status_code=503, detail=str(error)) from None


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
    except OrderServiceError as error:
        # Java 订单服务故障不是“订单不存在”，也不是模型错误。
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

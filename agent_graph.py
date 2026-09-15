"""OpsPilot 的 LangGraph Agent：模型选工具，图负责执行和安全状态转换。

模型不能直接改工单。它只能提出 request_priority_change；图会暂停等待用户确认，
然后由受控的 PendingActionStore 执行一次写操作。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Literal, Protocol, TypedDict

import httpx
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, ConfigDict, Field

from action_models import PendingAction
from evidence_review import EvidenceReviewer, verify_evidence_review
from grounding_policy import answer_when_evidence_is_missing, append_verified_citations
from order_service_client import OrderServiceError
from preview_tool_call import build_initial_messages, extract_tool_preview, load_api_key
from retry_policy import ModelRequestTelemetry, request_message_with_retry
from tool_args import RequestPriorityChangeArgs
from tool_executor import execute_tool
from tool_messages import build_tool_message
from return_review_client import ReasonCode, ReturnReviewRequest, ReturnReviewResponse
from return_draft_client import (
    ReturnApplicationResponse,
    ReturnDraftExpired,
    ReturnOrderChanged,
    ReturnDraftResponse,
)


AgentMode = Literal["mock", "live"]
AgentToolExecutor = Callable[[str, str], dict | list[dict] | None]
MAX_TOOL_STEPS = 3
AgentGraphStatus = Literal[
    "RUNNING",
    "WAITING_CONFIRMATION",
    "WAITING_REASON",
    "STALE_CONFIRMATION",
    "COMPLETED",
    "CANCELLED",
    "EXPIRED",
]


class AgentGraphInputError(ValueError):
    """模拟模型无法从用户输入构造安全工具调用。"""


class AgentGraphConfigurationError(ValueError):
    """真实模型模式缺少本地配置，不能静默降级到 mock。"""


class AgentGraphNotFoundError(LookupError):
    """要恢复的 thread_id 不存在。"""


class AgentGraphStateError(ValueError):
    """工作流存在，但当前不允许执行 resume。"""


class AgentActionStore(Protocol):
    def propose_priority_change(self, ticket_id: str, new_priority: str) -> PendingAction: ...

    def confirm(self, action_id: str) -> PendingAction: ...

    def cancel(self, action_id: str) -> PendingAction: ...


class AgentModelGateway(Protocol):
    """图不依赖某个厂商 SDK；节点只要求“给消息，返回一条模型消息”。"""

    def request(
        self, mode: AgentMode, messages: list[dict], *, offer_tools: bool
    ) -> dict | "AgentModelReply": ...


@dataclass(frozen=True)
class AgentModelReply:
    """模型消息和该轮 HTTP 指标；测试网关仍可直接返回旧字典。"""

    message: dict
    telemetry: ModelRequestTelemetry


class AgentGraphState(TypedDict, total=False):
    """LangGraph 的内部 State：不是 HTTP 请求体，也不会直接暴露给前端。"""

    thread_id: str
    mode: AgentMode
    messages: list[dict]
    status: AgentGraphStatus
    tool_name: str
    arguments_json: str
    tool_call_id: str
    tool_result: dict | list[dict] | None
    action_id: str
    # await_approval 节点返回的用户决定，供后续条件边选择执行或取消。
    approved: bool
    answer: str
    tool_steps: int
    tool_trace: list[dict]
    rag_evidence: list[dict]
    evidence_review: dict
    model_requests: int
    simulated_model_requests: int
    model_http_attempts: int
    model_retry_count: int
    model_turn_durations_ms: list[float]
    model_http_attempt_durations_ms: list[float]
    order_id: str
    return_reason: str | None
    return_decision: str
    reason_code: ReasonCode
    return_review: dict
    return_draft: dict
    return_application: dict


class AgentGraphStartRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    thread_id: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=1000)
    mode: AgentMode = "mock"
    return_reason: str | None = Field(default=None, min_length=1, max_length=1000)
    reason_code: ReasonCode = "OTHER"


class ReturnReasonRequest(ReturnReviewRequest):
    """补充原因只接受文本，不允许客户端替换订单或资格结果。"""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    return_reason: str = Field(min_length=1, max_length=1000)


class AgentGraphResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool


class AgentToolTrace(BaseModel):
    """公开的执行轨迹；不包含系统提示词、密钥或原始模型消息。"""

    step: int = Field(ge=1)
    tool_name: str
    result: dict | list[dict] | None


class AgentGraphResponse(BaseModel):
    """FastAPI 的公开响应；不返回完整 prompt、原始模型消息或任何密钥。"""

    thread_id: str
    mode: AgentMode
    status: AgentGraphStatus
    answer: str
    tool_name: str
    tool_result: dict | list[dict] | None
    action_id: str | None = None
    order_id: str | None = None
    return_reason: str | None = None
    return_review: ReturnReviewResponse | None = None
    return_draft: ReturnDraftResponse | None = None
    return_application: ReturnApplicationResponse | None = None
    tool_steps: int = Field(ge=0)
    tool_trace: list[AgentToolTrace] = Field(default_factory=list)
    model_requests: int = Field(ge=0)
    simulated_model_requests: int = Field(ge=0)
    model_http_attempts: int = Field(default=0, ge=0)
    model_retry_count: int = Field(default=0, ge=0)
    model_turn_durations_ms: list[float] = Field(default_factory=list)
    model_http_attempt_durations_ms: list[float] = Field(default_factory=list)


class ConfiguredAgentModelGateway:
    """mock 全离线；live 才读取本地 .env 并请求模型。"""

    def request(
        self, mode: AgentMode, messages: list[dict], *, offer_tools: bool
    ) -> dict | AgentModelReply:
        if mode == "mock":
            return self._mock_response(messages, offer_tools=offer_tools)
        if mode != "live":  # TypedDict 之外的调用也要拒绝，避免隐式行为。
            raise AgentGraphInputError("不支持的 Agent 模式。")

        try:
            api_key = load_api_key()
        except (OSError, ValueError):
            raise AgentGraphConfigurationError(
                "真实模式未配置可用密钥；请只在本地 .env 中设置。"
            ) from None

        # API Key 只停留在这一小段请求代码中，不进入 LangGraph Checkpoint State。
        with httpx.Client(timeout=httpx.Timeout(45, connect=10), trust_env=False) as client:
            telemetry = ModelRequestTelemetry()
            try:
                message = request_message_with_retry(
                    api_key,
                    client,
                    messages,
                    offer_tools=offer_tools,
                    telemetry=telemetry,
                )
            except Exception as error:
                # 异常继续保持原类型，供现有 API 映射处理；只附加数值诊断，不附加请求正文。
                error.model_http_attempts = telemetry.http_attempts
                error.model_retry_count = telemetry.retry_count
                error.model_turn_durations_ms = [telemetry.duration_ms]
                error.model_http_attempt_durations_ms = telemetry.attempt_durations_ms.copy()
                raise
            return AgentModelReply(message=message, telemetry=telemetry)

    def _mock_response(self, messages: list[dict], *, offer_tools: bool) -> dict:
        """离线模拟“模型选择工具/根据工具结果回答”，用于稳定开发和测试。"""
        observations = self._mock_observations(messages)
        if offer_tools:
            question = self._last_user_message(messages)
            next_call = (
                self._select_mock_tool(question)
                if not observations
                else self._select_mock_follow_up(question, observations)
            )
            if next_call is not None:
                tool_name, arguments = next_call
                return {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": f"mock_call_{len(observations) + 1:03d}",
                        "type": "function",
                        "function": {"name": tool_name, "arguments": json.dumps(arguments)},
                    }],
                }

        # 最终回答只能使用程序写回 messages 的工具结果，不能自行编造业务数据。
        return {"role": "assistant", "content": self._build_mock_answer(observations)}

    @staticmethod
    def _mock_observations(messages: list[dict]) -> list[tuple[str, object]]:
        """按 tool_call_id 配对模型调用与工具结果，支持多步轨迹。"""
        pending_names: dict[str, str] = {}
        observations: list[tuple[str, object]] = []
        for message in messages:
            for call in message.get("tool_calls", []) or []:
                if call.get("type") == "function" and isinstance(call.get("id"), str):
                    pending_names[call["id"]] = call.get("function", {}).get("name", "unknown")
            if message.get("role") == "tool" and message.get("tool_call_id") in pending_names:
                observations.append((
                    pending_names[message["tool_call_id"]],
                    json.loads(message["content"]),
                ))
        return observations

    @staticmethod
    def _select_mock_follow_up(
        question: str, observations: list[tuple[str, object]]
    ) -> tuple[str, dict] | None:
        """只模拟一个可验收的两步场景；不声称具备真实模型规划能力。"""
        completed = {name for name, _ in observations}
        asks_refund_timing = any(text in question for text in ("退款多久到账", "退款时间", "退款到账"))
        if "query_order" in completed and "search_knowledge_base" not in completed and asks_refund_timing:
            return "search_knowledge_base", {"query": "退款多久到账", "limit": 3}
        return None

    @staticmethod
    def _build_mock_answer(observations: list[tuple[str, object]]) -> str:
        if not observations:
            return "[模拟模式] 未执行工具，无法生成有依据的回答。"
        parts: list[str] = []
        for tool_name, tool_result in observations:
            if tool_result is None:
                parts.append("没有找到对应记录。")
            elif tool_name == "query_ticket":
                parts.append(
                    f"工单 {tool_result['id']}：状态 {tool_result['status']}，"
                    f"优先级 {tool_result['priority']}。"
                )
            elif tool_name == "query_order":
                parts.append(
                    f"订单 {tool_result['id']}：商品 {tool_result['product']}，"
                    f"状态 {tool_result['status']}。"
                )
            elif tool_name == "check_return_eligibility":
                decisions = {
                    "NO_REASON_ALLOWED": "该订单在七天无理由期限内，可以申请退货。",
                    "REASON_REQUIRED": "该订单可以申请退货，但必须提供退货理由。",
                    "NOT_ALLOWED": "该订单当前不能申请退货。",
                }
                parts.append(decisions[tool_result["decision"]])
            elif tool_name == "search_knowledge_base" and tool_result:
                parts.append(f"根据知识库：{tool_result[0]['content']}")
        return "[模拟模式] " + " ".join(parts)

    @staticmethod
    def _last_user_message(messages: list[dict]) -> str:
        for message in reversed(messages):
            if message.get("role") == "user" and isinstance(message.get("content"), str):
                return message["content"]
        raise AgentGraphInputError("缺少用户消息。")

    @staticmethod
    def _select_mock_tool(question: str) -> tuple[str, dict]:
        ids = set(re.findall(r"(?<![A-Za-z0-9_-])[TO]-[0-9]{4}(?![A-Za-z0-9_-])", question))
        if len(ids) > 1:
            raise AgentGraphInputError("模拟模式每次只支持一个工单或订单编号。")

        change_requested = any(word in question for word in ("改", "设置", "调整"))
        return_requested = any(word in question for word in ("退货", "无理由", "能退", "可以退"))
        priorities = {
            value.lower()
            for value in re.findall(r"(?<![A-Za-z])(low|medium|high)(?![A-Za-z])", question, re.I)
        }
        if ids:
            record_id = ids.pop()
            if change_requested:
                if not record_id.startswith("T-") or len(priorities) != 1:
                    raise AgentGraphInputError("修改需要一个工单编号和一个 low、medium 或 high 优先级。")
                return "request_priority_change", {
                    "ticket_id": record_id,
                    "new_priority": priorities.pop(),
                }
            if record_id.startswith("O-") and return_requested:
                return "check_return_eligibility", {"order_id": record_id}
            if record_id.startswith("T-"):
                return "query_ticket", {"ticket_id": record_id}
            return "query_order", {"order_id": record_id}

        # 没有业务编号时，作为知识问题走 RAG；检索层会决定有无足够证据。
        if len(question.strip()) >= 2:
            return "search_knowledge_base", {"query": question.strip(), "limit": 3}
        raise AgentGraphInputError("请提供工单/订单编号，或提出至少两个字的知识库问题。")


def build_agent_graph(
    action_store: AgentActionStore,
    model_gateway: AgentModelGateway,
    checkpointer=None,
    draft_gateway=None,
    tool_runner: AgentToolExecutor = execute_tool,
    evidence_reviewer: EvidenceReviewer | None = None,
):
    """构建一个单 Agent 图；节点由 Python 函数构成，边定义下一步。"""

    def call_model_node(state: AgentGraphState) -> AgentGraphState:
        # 达到上限后不再向模型提供工具，强制进入最终回答，防止无限循环。
        offer_tools = state.get("tool_steps", 0) < MAX_TOOL_STEPS
        gateway_reply = model_gateway.request(
            state["mode"], state["messages"], offer_tools=offer_tools
        )
        # 项目真实网关返回消息和 HTTP 指标；旧测试网关仍可只返回消息字典。
        if isinstance(gateway_reply, AgentModelReply):
            message = gateway_reply.message
            telemetry = gateway_reply.telemetry
        else:
            message = gateway_reply
            telemetry = None
        counter_name = "simulated_model_requests" if state["mode"] == "mock" else "model_requests"
        updates: AgentGraphState = {
            counter_name: state.get(counter_name, 0) + 1,
        }
        if telemetry is not None:
            updates.update({
                "model_http_attempts": state.get("model_http_attempts", 0) + telemetry.http_attempts,
                "model_retry_count": state.get("model_retry_count", 0) + telemetry.retry_count,
                "model_turn_durations_ms": state.get("model_turn_durations_ms", [])
                + [telemetry.duration_ms],
                "model_http_attempt_durations_ms": state.get(
                    "model_http_attempt_durations_ms", []
                ) + telemetry.attempt_durations_ms,
            })

        if message.get("role") != "assistant":
            raise ValueError("模型消息角色不正确。")
        calls = message.get("tool_calls")
        if not calls:
            answer = message.get("content")
            if not isinstance(answer, str) or not answer.strip():
                raise ValueError("模型没有返回非空最终回答。")
            evidence = state.get("rag_evidence", [])
            if evidence:
                answer = append_verified_citations(answer, evidence)
            updates.update({"answer": answer, "status": "COMPLETED"})
            return updates

        if not offer_tools:
            raise ValueError("已达到工具调用上限，模型仍返回工具调用。")

        preview = extract_tool_preview(message)
        tool_call = calls[0]
        tool_call_id = tool_call.get("id")
        if tool_call.get("type") != "function" or not isinstance(tool_call_id, str) or not tool_call_id.strip():
            raise ValueError("模型工具调用缺少有效类型或调用 ID。")
        updates.update({
            "messages": state["messages"] + [message],
            "tool_name": preview["name"],
            "arguments_json": preview["arguments"],
            "tool_call_id": tool_call_id,
            "tool_steps": state.get("tool_steps", 0) + 1,
            "status": "RUNNING",
        })
        return updates

    def route_after_model(state: AgentGraphState) -> Literal["read_tool", "propose_change", "end"]:
        if state["status"] == "COMPLETED":
            return "end"
        if state["tool_name"] == "request_priority_change":
            return "propose_change"
        return "read_tool"

    def read_tool_node(state: AgentGraphState) -> AgentGraphState:
        # 工具执行函数可以按运行环境注入，离线评测无需修改模块级全局变量。
        result = tool_runner(state["tool_name"], state["arguments_json"])
        trace = state.get("tool_trace", []) + [{
            "step": state["tool_steps"],
            "tool_name": state["tool_name"],
            "result": result,
        }]
        evidence = state.get("rag_evidence", [])
        review_updates = {}
        if state["tool_name"] == "search_knowledge_base":
            if not isinstance(result, list):
                raise ValueError("知识库工具结果不符合列表契约。")
            if result and evidence_reviewer is not None:
                question = next(
                    message["content"] for message in reversed(state["messages"])
                    if message.get("role") == "user"
                )
                raw_verdict = evidence_reviewer(question, deepcopy(result))
                verdict, result = verify_evidence_review(raw_verdict, result)
                review_updates["evidence_review"] = verdict.model_dump()
            evidence = evidence + result
        if state["tool_name"] == "check_return_eligibility" and result is not None:
            # 工具结果单独提交为 Checkpoint，恢复追问时不会再次执行查询。
            updates = {
                "tool_result": result,
                "order_id": result["order_id"],
                "return_decision": result["decision"],
                "messages": state["messages"] + [build_tool_message(state["tool_call_id"], result)],
                "tool_trace": trace,
                "rag_evidence": evidence,
            }
            if result["decision"] == "REASON_REQUIRED" and state.get("return_reason") is None:
                updates.update({
                    "status": "WAITING_REASON",
                    "answer": f"订单 {result['order_id']} 已超过七天无理由期限，请说明退货原因。",
                })
            return updates
        no_evidence_answer = answer_when_evidence_is_missing(state["tool_name"], result)
        if no_evidence_answer is not None:
            return {
                **review_updates,
                "tool_result": result,
                "answer": no_evidence_answer,
                "status": "COMPLETED",
                "tool_trace": trace,
                "rag_evidence": evidence,
            }
        return {
            **review_updates,
            "tool_result": result,
            "messages": state["messages"] + [build_tool_message(state["tool_call_id"], result)],
            "tool_trace": trace,
            "rag_evidence": evidence,
        }

    def route_after_read(state: AgentGraphState) -> str:
        # 两种可申请资格都先建立服务器草稿：七天内直接进入确认，八至十五天先追问原因。
        if state.get("return_decision") in {"NO_REASON_ALLOWED", "REASON_REQUIRED"}:
            return "prepare_return_draft"
        return "end" if state["status"] == "COMPLETED" else "call_model"

    def prepare_return_draft_node(state: AgentGraphState) -> AgentGraphState:
        if draft_gateway is None:
            raise AgentGraphConfigurationError("未配置 Java 限时草稿服务")
        draft = ReturnDraftResponse.model_validate(draft_gateway.start(state["order_id"]))
        if draft.order_id != state["order_id"]:
            raise ValueError("草稿订单不匹配")
        if draft.status in {"REVIEWED", "SUBMITTED", "CANCELLED"}:
            raise AgentGraphStateError("该订单的草稿已处理，请查询原会话。")
        waiting_status = (
            "WAITING_CONFIRMATION"
            if state["return_decision"] == "NO_REASON_ALLOWED"
            else "WAITING_REASON"
        )
        return {
            "return_draft": draft.model_dump(mode="json"),
            "status": "EXPIRED" if draft.status == "EXPIRED" else waiting_status,
            "answer": "退货草稿已过期，不能继续补充。" if draft.status == "EXPIRED"
                      else (
                          "退货申请内容已准备，请确认是否创建申请。"
                          if waiting_status == "WAITING_CONFIRMATION"
                          else "请在草稿创建后1小时内补充原因；超时草稿失效。"
                      ),
        }

    def route_after_draft(state: AgentGraphState) -> str:
        if state["status"] == "EXPIRED":
            return "end"
        if state["status"] == "WAITING_CONFIRMATION":
            return "await_return_confirmation"
        return "ask_return_reason" if state["return_reason"] is None else "finish_reason_collection"

    def ask_return_reason_node(state: AgentGraphState) -> AgentGraphState:
        # interrupt 首次暂停；Command 恢复后，它返回用户提交的数据。
        resumed = interrupt({"order_id": state["order_id"], "message": state["answer"]})
        request = ReturnReasonRequest.model_validate(resumed)
        return {
            "return_reason": request.return_reason,
            "reason_code": request.reason_code,
            "messages": state["messages"] + [{"role": "user", "content": request.return_reason}],
            "status": "RUNNING",
        }

    def finish_reason_collection_node(state: AgentGraphState) -> AgentGraphState:
        # Java 校验草稿截止时间；跨日不直接套用新的签收天数。
        try:
            review = ReturnReviewResponse.model_validate(draft_gateway.submit(
                state["order_id"], state["return_draft"]["draft_id"],
                state["return_reason"], state["reason_code"]))
        except ReturnDraftExpired:
            return {"status": "EXPIRED", "return_draft": {**state["return_draft"], "status": "EXPIRED"},
                    "answer": "退货草稿已超过1小时有效期，补充原因未被受理。"}
        answers = {
            "ACCEPTABLE": "预审可受理，可进入申请确认；尚未创建退货申请。",
            "REJECTED": "预审不可受理；尚未创建退货申请。",
            "MANUAL_REVIEW": "原因已记录，需要人工核实材料；尚未创建退货申请，也未创建人工审核工单。",
        }
        next_status = "WAITING_CONFIRMATION" if review.decision == "ACCEPTABLE" else "COMPLETED"
        return {
            "status": next_status,
            "return_review": review.model_dump(),
            "return_draft": {**state["return_draft"], "status": "REVIEWED"},
            "answer": f"订单 {state['order_id']}：" + answers[review.decision],
        }

    def route_after_return_review(state: AgentGraphState) -> str:
        return "await_return_confirmation" if state["status"] == "WAITING_CONFIRMATION" else "end"

    def await_return_confirmation_node(state: AgentGraphState) -> AgentGraphState:
        draft = state["return_draft"]
        resumed = interrupt({
            "tool_name": "create_return_application",
            "draft_id": draft["draft_id"],
            "order_id": state["order_id"],
            "product": draft["product"],
            "refund_amount_cents": draft["amount_cents"],
            "return_reason": state.get("return_reason"),
            "expires_at": draft["expires_at"],
            "message": "确认后将创建退货申请，是否继续？",
        })
        if not isinstance(resumed, dict) or not isinstance(resumed.get("approved"), bool):
            raise ValueError("恢复 Agent 时必须提供布尔类型的 approved。")
        return {"approved": resumed["approved"]}

    def route_after_return_confirmation(state: AgentGraphState) -> str:
        return "execute_return" if state["approved"] else "cancel_return"

    def execute_return_node(state: AgentGraphState) -> AgentGraphState:
        try:
            application = ReturnApplicationResponse.model_validate(draft_gateway.confirm(
                state["order_id"], state["return_draft"]["draft_id"]
            ))
        except ReturnOrderChanged as error:
            # 旧确认只授权旧快照；把冲突保存成明确状态，页面刷新后也不能继续显示“等待确认”。
            return {
                "status": "STALE_CONFIRMATION",
                "answer": str(error),
            }
        except ReturnDraftExpired:
            return {
                "status": "EXPIRED",
                "return_draft": {**state["return_draft"], "status": "EXPIRED"},
                "answer": "确认时草稿已超过1小时，未创建退货申请。",
            }
        return {
            "status": "COMPLETED",
            "return_application": application.model_dump(mode="json"),
            "return_draft": {**state["return_draft"], "status": "SUBMITTED"},
            "answer": f"退货申请 {application.application_id} 已创建，状态为 SUBMITTED。",
        }

    def cancel_return_node(state: AgentGraphState) -> AgentGraphState:
        draft = ReturnDraftResponse.model_validate(draft_gateway.cancel(
            state["order_id"], state["return_draft"]["draft_id"]
        ))
        return {
            "status": "CANCELLED",
            "return_draft": draft.model_dump(mode="json"),
            "answer": "已取消本次退货草稿，未创建退货申请。",
        }

    def propose_change_node(state: AgentGraphState) -> AgentGraphState:
        # Pydantic 在真正创建 PendingAction 前验证模型参数，禁止模型夹带字段或伪造优先级。
        args = RequestPriorityChangeArgs.model_validate_json(state["arguments_json"])
        action = action_store.propose_priority_change(args.ticket_id, args.new_priority)
        return {
            "action_id": action.action_id,
            "tool_result": action.model_dump(),
            "tool_trace": state.get("tool_trace", []) + [{
                "step": state["tool_steps"],
                "tool_name": state["tool_name"],
                "result": action.model_dump(),
            }],
            "status": "WAITING_CONFIRMATION",
            "answer": f"操作 {action.action_id} 等待确认，尚未修改工单。",
        }

    def await_approval_node(state: AgentGraphState) -> AgentGraphState:
        # 节点暂停前没有写操作；恢复时本节点从头执行也不会重复创建 PendingAction。
        resumed = interrupt({
            "action_id": state["action_id"],
            "tool_name": state["tool_name"],
            "message": "该操作会修改工单优先级，是否确认？",
        })
        if not isinstance(resumed, dict) or not isinstance(resumed.get("approved"), bool):
            raise ValueError("恢复 Agent 时必须提供布尔类型的 approved。")
        return {"approved": resumed["approved"]}

    def route_after_approval(state: AgentGraphState) -> Literal["execute_change", "cancel_change"]:
        return "execute_change" if state["approved"] else "cancel_change"

    def execute_change_node(state: AgentGraphState) -> AgentGraphState:
        action = action_store.confirm(state["action_id"])
        return {
            "tool_result": action.model_dump(),
            "status": "COMPLETED",
            "answer": f"已执行操作 {action.action_id}；工单优先级已改为 {action.new_priority}。",
        }

    def cancel_change_node(state: AgentGraphState) -> AgentGraphState:
        action = action_store.cancel(state["action_id"])
        return {
            "tool_result": action.model_dump(),
            "status": "CANCELLED",
            "answer": f"已取消操作 {action.action_id}；工单优先级未修改。",
        }

    builder = StateGraph(AgentGraphState)
    builder.add_node("call_model", call_model_node)
    builder.add_node("read_tool", read_tool_node)
    builder.add_node("prepare_return_draft", prepare_return_draft_node)
    builder.add_conditional_edges("prepare_return_draft", route_after_draft,
        {"end": END, "ask_return_reason": "ask_return_reason",
         "finish_reason_collection": "finish_reason_collection",
         "await_return_confirmation": "await_return_confirmation"})
    builder.add_node("ask_return_reason", ask_return_reason_node)
    builder.add_node("finish_reason_collection", finish_reason_collection_node)
    builder.add_edge("ask_return_reason", "finish_reason_collection")
    builder.add_conditional_edges(
        "finish_reason_collection", route_after_return_review,
        {"await_return_confirmation": "await_return_confirmation", "end": END},
    )
    builder.add_node("await_return_confirmation", await_return_confirmation_node)
    builder.add_node("execute_return", execute_return_node)
    builder.add_node("cancel_return", cancel_return_node)
    builder.add_conditional_edges(
        "await_return_confirmation", route_after_return_confirmation,
        {"execute_return": "execute_return", "cancel_return": "cancel_return"},
    )
    builder.add_edge("execute_return", END)
    builder.add_edge("cancel_return", END)
    builder.add_node("propose_change", propose_change_node)
    builder.add_node("await_approval", await_approval_node)
    builder.add_node("execute_change", execute_change_node)
    builder.add_node("cancel_change", cancel_change_node)
    builder.add_edge(START, "call_model")
    builder.add_conditional_edges(
        "call_model",
        route_after_model,
        {"read_tool": "read_tool", "propose_change": "propose_change", "end": END},
    )
    builder.add_conditional_edges(
        "read_tool",
        route_after_read,
        {"call_model": "call_model", "end": END,
         "prepare_return_draft": "prepare_return_draft"},
    )
    builder.add_edge("propose_change", "await_approval")
    builder.add_conditional_edges(
        "await_approval",
        route_after_approval,
        {"execute_change": "execute_change", "cancel_change": "cancel_change"},
    )
    builder.add_edge("execute_change", END)
    builder.add_edge("cancel_change", END)
    saver = checkpointer if checkpointer is not None else InMemorySaver()
    return builder.compile(checkpointer=saver)


def start_agent_graph(graph, request: AgentGraphStartRequest) -> AgentGraphResponse:
    # 用与旧 Agent 相同的系统提示词创建全新消息，避免跨会话污染历史。
    messages = build_initial_messages(request.message)
    # 为这次 HTTP 请求构造 LangGraph 查找和保存 Checkpoint 所需的 thread_id。
    config = {"configurable": {"thread_id": request.thread_id}}
    if graph.get_state(config).values:
        raise AgentGraphStateError("该 thread_id 已使用，请恢复已有流程或使用新编号。")
    # 从 START 运行；查询会运行到 END，写操作会运行到 interrupt 后返回。
    state = graph.invoke({
        # 把公开 thread_id 写入内部 State，便于构造稳定的 API 响应。
        "thread_id": request.thread_id,
        # mode 决定模型网关使用离线模拟还是读取本地密钥请求真实模型。
        "mode": request.mode,
        # State 保存系统提示词和用户问题，后续工具结果会继续追加到这个列表。
        "messages": messages,
        # 工作流刚开始时尚未选择工具，因此状态为 RUNNING。
        "status": "RUNNING",
        "return_reason": request.return_reason,
        "reason_code": request.reason_code,
        # 工具步数与公开轨迹从空开始，最多执行 MAX_TOOL_STEPS 次。
        "tool_steps": 0,
        "tool_trace": [],
        # 累计所有 RAG 结果，最终由程序附加可信引用。
        "rag_evidence": [],
        # 真实模型请求计数从零开始，且不会由 mock 模式增加。
        "model_requests": 0,
        # 模拟模式单独记录本地模拟的模型交互次数。
        "simulated_model_requests": 0,
        # HTTP 指标只统计 live 网关；mock 模式不会伪造外部请求次数。
        "model_http_attempts": 0,
        "model_retry_count": 0,
        "model_turn_durations_ms": [],
        "model_http_attempt_durations_ms": [],
    }, config=config)
    # 不把 LangGraph 的内部字段直接返回给 HTTP 客户端。
    return _to_response(state)


def resume_agent_graph(graph, thread_id: str, request: AgentGraphResumeRequest) -> AgentGraphResponse:
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = graph.get_state(config)
    if "thread_id" not in snapshot.values:
        raise AgentGraphNotFoundError("Agent 工作流不存在，或该 thread_id 从未启动。")
    waiting_nodes = {"await_approval", "await_return_confirmation"}
    if not waiting_nodes.intersection(snapshot.next):
        raise AgentGraphStateError("Agent 当前不在等待确认，不能恢复。")
    state = graph.invoke(Command(resume={"approved": request.approved}), config=config)
    return _to_response(state)


def submit_return_reason(graph, thread_id: str, request: ReturnReasonRequest) -> AgentGraphResponse:
    """同一 thread 恢复追问；顺序重复提交被拒绝，不重新执行查询。"""
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = graph.get_state(config)
    if "thread_id" not in snapshot.values:
        raise AgentGraphNotFoundError("Agent 工作流不存在。")
    if "finish_reason_collection" in snapshot.next:
        # 原因已存储而预审失败时，只重试只读审核节点，不能偷偷修改之前提交的信息。
        if (snapshot.values.get("return_reason") != request.return_reason
                or snapshot.values.get("reason_code") != request.reason_code):
            raise AgentGraphStateError("重试预审时须使用已保存的原因和类别。")
        return _to_response(graph.invoke(None, config=config))
    if "ask_return_reason" not in snapshot.next:
        raise AgentGraphStateError("Agent 当前不在等待退货原因。")
    state = graph.invoke(Command(resume=request.model_dump()), config=config)
    return _to_response(state)


def refresh_return_confirmation(graph, thread_id: str, draft_gateway) -> AgentGraphResponse:
    """用最新订单重建草稿，并让同一会话重新进入人工确认或原因追问。"""
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = graph.get_state(config)
    if "thread_id" not in snapshot.values:
        raise AgentGraphNotFoundError("Agent 工作流不存在。")
    if snapshot.values.get("status") != "STALE_CONFIRMATION":
        raise AgentGraphStateError("只有确认内容已失效的退货流程才能刷新。")

    order_id = snapshot.values["order_id"]
    eligibility_result = execute_tool(
        "check_return_eligibility",
        json.dumps({"order_id": order_id}),
    )
    if not isinstance(eligibility_result, dict):
        raise OrderServiceError("最新退货资格结果不符合契约。")
    if not eligibility_result["can_apply"]:
        graph.update_state(config, {
            "status": "COMPLETED",
            "tool_result": eligibility_result,
            "return_decision": eligibility_result["decision"],
            "return_application": None,
            "answer": f"订单 {order_id} 当前已不符合退货条件，未创建退货申请。",
        }, as_node="execute_return")
        return _to_response(graph.get_state(config).values)

    old_draft_id = snapshot.values["return_draft"]["draft_id"]
    draft = ReturnDraftResponse.model_validate(
        draft_gateway.refresh(order_id, old_draft_id)
    )
    if draft.order_id != order_id or str(draft.draft_id) == old_draft_id:
        raise OrderServiceError("刷新后的退货草稿不符合契约。")

    waiting_status = (
        "WAITING_CONFIRMATION"
        if eligibility_result["decision"] == "NO_REASON_ALLOWED"
        else "WAITING_REASON"
    )
    graph.update_state(config, {
        "status": waiting_status,
        "tool_result": eligibility_result,
        "return_decision": eligibility_result["decision"],
        "return_draft": draft.model_dump(mode="json"),
        "return_reason": None,
        "reason_code": "OTHER",
        "return_review": None,
        "return_application": None,
        "approved": False,
        "answer": (
            "订单信息已刷新，请核对最新内容后重新确认。"
            if waiting_status == "WAITING_CONFIRMATION"
            else "订单信息已刷新，最新资格要求重新填写退货原因。"
        ),
    }, as_node="prepare_return_draft")
    state = graph.invoke(None, config=config)
    return _to_response(state)


def get_agent_graph_state(graph, thread_id: str, draft_gateway=None) -> AgentGraphResponse:
    """读取已保存的 State，供前端刷新、轮询或恢复页面时展示当前运行状态。"""
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = graph.get_state(config)
    if "thread_id" not in snapshot.values:
        raise AgentGraphNotFoundError("Agent 工作流不存在，或该 thread_id 从未启动。")
    if (snapshot.values.get("status") in {"WAITING_REASON", "WAITING_CONFIRMATION"}
            and snapshot.values.get("return_draft") and draft_gateway):
        draft = draft_gateway.get(snapshot.values["order_id"], snapshot.values["return_draft"]["draft_id"])
        if draft["status"] == "EXPIRED":
            return _to_response({**snapshot.values, "status": "EXPIRED", "return_draft": draft,
                                 "answer": "退货草稿已过期，不能继续创建退货申请。"})
    return _to_response(snapshot.values)


def _to_response(state: AgentGraphState) -> AgentGraphResponse:
    return AgentGraphResponse(
        thread_id=state["thread_id"],
        mode=state["mode"],
        status=state["status"],
        answer=state["answer"],
        tool_name=state.get("tool_name", "no_tool"),
        tool_result=state.get("tool_result"),
        action_id=state.get("action_id"),
        order_id=state.get("order_id"),
        return_reason=state.get("return_reason"),
        return_review=state.get("return_review"),
        return_draft=state.get("return_draft"),
        return_application=state.get("return_application"),
        tool_steps=state.get("tool_steps", 0),
        tool_trace=state.get("tool_trace", []),
        model_requests=state.get("model_requests", 0),
        simulated_model_requests=state.get("simulated_model_requests", 0),
        model_http_attempts=state.get("model_http_attempts", 0),
        model_retry_count=state.get("model_retry_count", 0),
        model_turn_durations_ms=state.get("model_turn_durations_ms", []),
        model_http_attempt_durations_ms=state.get("model_http_attempt_durations_ms", []),
    )

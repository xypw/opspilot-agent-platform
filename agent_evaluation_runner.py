"""批量执行 Agent 标准用例，并把公开响应交给评测器。"""

import json
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from agent_evaluation import (
    AgentEvaluationCase,
    AgentEvaluationResult,
    AgentEvaluationSummary,
    build_evaluation_summary,
    evaluate_response,
)
from agent_graph import (
    AgentGraphResponse,
    AgentGraphStartRequest,
    AgentMode,
    start_agent_graph,
)


# Runner 只接收用例编号和用户问题，故意不接收标准答案，防止评测数据泄漏。
AgentRunner = Callable[[str, str], AgentGraphResponse]
ThreadIdFactory = Callable[[str], str]
ReturnApplicationProbe = Callable[[AgentGraphResponse], bool]


def load_evaluation_cases(path: str | Path) -> list[AgentEvaluationCase]:
    """从 JSON 文件加载并校验一批 Agent 评测用例。"""
    # Path 同时支持调用方传入字符串路径或 Path 对象。
    cases_path = Path(path)
    # 明确指定 UTF-8，避免 Windows 默认编码导致中文读取失败。
    raw_cases = json.loads(cases_path.read_text(encoding="utf-8"))

    # 文件顶层必须是数组；字典虽然也是可迭代对象，但不符合评测集契约。
    if not isinstance(raw_cases, list):
        raise ValueError("Agent 评测文件顶层必须是 JSON 数组。")

    # Pydantic 逐条检查字段、状态类型、非空工具列表和额外字段。
    cases = [AgentEvaluationCase.model_validate(item) for item in raw_cases]
    # 重复编号会让报告无法唯一定位失败用例，因此在执行模型前直接拒绝。
    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("Agent 评测用例的 case_id 不能重复。")
    return cases


def run_evaluation_cases(
    cases: list[AgentEvaluationCase],
    run_agent: AgentRunner,
    *,
    return_application_probe: ReturnApplicationProbe | None = None,
) -> AgentEvaluationSummary:
    """逐条运行 Agent；单条异常记为失败，但不会中断剩余评测。"""
    # 每条用例产生一条结构化结果，最后统一计算成功率和失败原因分布。
    results: list[AgentEvaluationResult] = []
    for case in cases:
        # 使用单调时钟测量耗时，不受系统时间被用户或网络校时修改的影响。
        started_at = perf_counter()
        try:
            # 只把编号和用户问题交给 Agent，不能把 expected_tools 等答案传进去。
            response = run_agent(case.case_id, case.question)
            application_created = (
                return_application_probe(response)
                if return_application_probe is not None
                else None
            )
        except Exception as error:
            # 批量任务的边界允许隔离单条异常；Exception 不会吞掉退出等系统信号。
            status_code, provider_code = _safe_error_codes(error)
            (model_requests, simulated_requests, http_attempts, retry_count,
             turn_durations, attempt_durations) = (
                _safe_model_telemetry(error)
            )
            results.append(AgentEvaluationResult(
                case_id=case.case_id,
                success=False,
                failure_reasons=["runner_error"],
                duration_ms=_elapsed_ms(started_at),
                error_type=type(error).__name__,
                error_status_code=status_code,
                provider_error_code=provider_code,
                model_requests=model_requests,
                simulated_model_requests=simulated_requests,
                model_http_attempts=http_attempts,
                model_retry_count=retry_count,
                model_turn_durations_ms=turn_durations,
                model_http_attempt_durations_ms=attempt_durations,
            ))
            continue

        # 正常响应沿用已经测试过的状态、工具顺序、必要事实和引用判断。
        result = evaluate_response(
            case,
            response,
            return_application_created=application_created,
        )
        results.append(result.model_copy(update={"duration_ms": _elapsed_ms(started_at)}))

    # 汇总逻辑只依赖结构化结果，不关心底层使用 Mock 还是真实模型。
    return build_evaluation_summary(results)


def build_java_return_application_probe(draft_gateway) -> ReturnApplicationProbe:
    """通过 Java 草稿查询接口确认正式退货申请是否实际存在。"""
    def probe(response: AgentGraphResponse) -> bool:
        if response.return_application is not None:
            return True
        if response.return_draft is None or response.order_id is None:
            return False
        draft = draft_gateway.get(
            response.order_id,
            str(response.return_draft.draft_id),
        )
        return draft["status"] == "SUBMITTED"

    return probe


def select_evaluation_cases(
    cases: list[AgentEvaluationCase],
    selected_case_ids: list[str],
) -> list[AgentEvaluationCase]:
    """按明确编号选择小批量用例；空列表表示运行全部。"""
    if not selected_case_ids:
        return cases
    if len(selected_case_ids) != len(set(selected_case_ids)):
        raise ValueError("--case-id 不能重复。")

    cases_by_id = {case.case_id: case for case in cases}
    unknown_ids = [case_id for case_id in selected_case_ids if case_id not in cases_by_id]
    if unknown_ids:
        raise ValueError(f"未知 Agent 评测用例：{', '.join(unknown_ids)}")
    # 按命令行指定顺序运行，方便从最简单的单工具场景开始逐步扩大。
    return [cases_by_id[case_id] for case_id in selected_case_ids]


def require_external_model_permission(mode: AgentMode, allowed: bool) -> None:
    """live 模式必须由调用方明确开启，避免无意发送数据或消耗额度。"""
    if mode == "live" and not allowed:
        raise PermissionError(
            "live 评测会把问题发送给外部模型；请确认数据范围后添加 --allow-external-model。"
        )


def build_langgraph_runner(
    graph,
    *,
    mode: AgentMode = "mock",
    thread_id_factory: ThreadIdFactory | None = None,
) -> AgentRunner:
    """把现有 LangGraph 启动函数适配成通用 AgentRunner。"""
    # 测试可以注入固定编号；实际运行默认给每条用例生成隔离的新会话。
    make_thread_id = thread_id_factory or _new_evaluation_thread_id

    def run_agent(case_id: str, question: str) -> AgentGraphResponse:
        # 每条用例使用独立 thread_id，防止 Checkpoint 和历史消息相互污染。
        thread_id = make_thread_id(case_id)
        request = AgentGraphStartRequest(
            thread_id=thread_id,
            message=question,
            mode=mode,
        )
        try:
            # 这里只负责启动工作流；是否成功由外层评测器依据标准答案判断。
            return start_agent_graph(graph, request)
        except Exception as error:
            # 失败节点不会提交更新，因此合并“最后成功 Checkpoint + 本轮异常指标”。
            _merge_checkpoint_telemetry(graph, thread_id, error)
            # 使用 bare raise 保留原异常类型和原始 Traceback。
            raise

    return run_agent


def _new_evaluation_thread_id(case_id: str) -> str:
    """生成可辨认且不超过接口长度限制的评测会话编号。"""
    # 只保留适合日志和 URL 的字符，并限制长度，后面再拼接随机后缀。
    safe_case_id = re.sub(r"[^A-Za-z0-9_-]+", "-", case_id).strip("-")[:40]
    # case_id 极端情况下可能全部是中文；此时仍生成合法的通用名称。
    readable_part = safe_case_id or "case"
    return f"eval-{readable_part}-{uuid4().hex[:12]}"


def _elapsed_ms(started_at: float) -> float:
    """把单调时钟差转换为便于报告阅读的毫秒数。"""
    return round((perf_counter() - started_at) * 1000, 3)


def _safe_error_codes(error: Exception) -> tuple[int | None, str | None]:
    """只提取有限格式的诊断码，不把任意异常消息写入报告。"""
    raw_status = getattr(error, "status_code", None)
    status_code = raw_status if isinstance(raw_status, int) and 100 <= raw_status <= 599 else None
    raw_provider_code = getattr(error, "provider_code", None)
    provider_code = (
        raw_provider_code
        if isinstance(raw_provider_code, str)
        and raw_provider_code.isascii()
        and raw_provider_code.isdigit()
        and len(raw_provider_code) <= 8
        else None
    )
    return status_code, provider_code


def _safe_model_telemetry(
    error: Exception,
) -> tuple[int, int, int, int, list[float], list[float]]:
    """从网关异常提取有限数值指标，拒绝任意对象和负数。"""
    model_requests = _safe_nonnegative_int(getattr(error, "model_requests", 0))
    simulated_requests = _safe_nonnegative_int(
        getattr(error, "simulated_model_requests", 0)
    )
    http_attempts = _safe_nonnegative_int(getattr(error, "model_http_attempts", 0))
    retry_count = _safe_nonnegative_int(getattr(error, "model_retry_count", 0))
    turn_durations = _safe_duration_list(getattr(error, "model_turn_durations_ms", []))
    attempt_durations = _safe_duration_list(
        getattr(error, "model_http_attempt_durations_ms", [])
    )
    return (
        model_requests,
        simulated_requests,
        http_attempts,
        retry_count,
        turn_durations,
        attempt_durations,
    )


def _merge_checkpoint_telemetry(graph, thread_id: str, error: Exception) -> None:
    """把已提交节点的指标合并到失败轮次异常；失败时保留原始异常。"""
    try:
        # 使用与 start_agent_graph 相同的配置读取这一条评测会话。
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = graph.get_state(config)
        values = getattr(snapshot, "values", None)
        if not isinstance(values, Mapping):
            return

        # 异常上的数值属于当前失败轮次，Checkpoint 属于此前成功轮次。
        (failed_model_requests, failed_simulated_requests, failed_attempts,
         failed_retries, failed_turn_durations, failed_attempt_durations) = (
            _safe_model_telemetry(error)
        )
        error.model_requests = (
            _safe_nonnegative_int(values.get("model_requests", 0))
            + failed_model_requests
        )
        error.simulated_model_requests = (
            _safe_nonnegative_int(values.get("simulated_model_requests", 0))
            + failed_simulated_requests
        )
        error.model_http_attempts = (
            _safe_nonnegative_int(values.get("model_http_attempts", 0))
            + failed_attempts
        )
        error.model_retry_count = (
            _safe_nonnegative_int(values.get("model_retry_count", 0))
            + failed_retries
        )
        checkpoint_turn_durations = _safe_duration_list(
            values.get("model_turn_durations_ms", [])
        )
        checkpoint_attempt_durations = _safe_duration_list(
            values.get("model_http_attempt_durations_ms", [])
        )
        error.model_turn_durations_ms = _safe_duration_list(
            checkpoint_turn_durations + failed_turn_durations
        )
        error.model_http_attempt_durations_ms = _safe_duration_list(
            checkpoint_attempt_durations + failed_attempt_durations
        )
    except Exception:
        # Checkpoint 可能与模型同时故障；诊断增强失败不能覆盖最初的模型异常。
        return


def _safe_nonnegative_int(value: object) -> int:
    """只接受真正的非负整数；bool 虽是 int 子类但不能作为计数。"""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _safe_duration_list(value: object) -> list[float]:
    """只保留最多 20 个非负有限耗时值，防止异常对象污染报告。"""
    if not isinstance(value, list) or len(value) > 20:
        return []
    durations: list[float] = []
    for item in value:
        if not isinstance(item, (int, float)) or isinstance(item, bool):
            return []
        duration = float(item)
        if duration < 0 or duration == float("inf") or duration != duration:
            return []
        durations.append(duration)
    return durations

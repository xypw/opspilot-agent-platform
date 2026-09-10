"""批量执行 Agent 标准用例，并把公开响应交给评测器。"""

import json
import re
from collections.abc import Callable
from pathlib import Path
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
) -> AgentEvaluationSummary:
    """逐条运行 Agent；单条异常记为失败，但不会中断剩余评测。"""
    # 每条用例产生一条结构化结果，最后统一计算成功率和失败原因分布。
    results: list[AgentEvaluationResult] = []
    for case in cases:
        try:
            # 只把编号和用户问题交给 Agent，不能把 expected_tools 等答案传进去。
            response = run_agent(case.case_id, case.question)
        except Exception as error:
            # 批量任务的边界允许隔离单条异常；Exception 不会吞掉退出等系统信号。
            results.append(AgentEvaluationResult(
                case_id=case.case_id,
                success=False,
                failure_reasons=["runner_error"],
                error_type=type(error).__name__,
            ))
            continue

        # 正常响应沿用已经测试过的状态、工具顺序、必要事实和引用判断。
        results.append(evaluate_response(case, response))

    # 汇总逻辑只依赖结构化结果，不关心底层使用 Mock 还是真实模型。
    return build_evaluation_summary(results)


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
        request = AgentGraphStartRequest(
            thread_id=make_thread_id(case_id),
            message=question,
            mode=mode,
        )
        # 这里只负责启动工作流；是否成功由外层评测器依据标准答案判断。
        return start_agent_graph(graph, request)

    return run_agent


def _new_evaluation_thread_id(case_id: str) -> str:
    """生成可辨认且不超过接口长度限制的评测会话编号。"""
    # 只保留适合日志和 URL 的字符，并限制长度，后面再拼接随机后缀。
    safe_case_id = re.sub(r"[^A-Za-z0-9_-]+", "-", case_id).strip("-")[:40]
    # case_id 极端情况下可能全部是中文；此时仍生成合法的通用名称。
    readable_part = safe_case_id or "case"
    return f"eval-{readable_part}-{uuid4().hex[:12]}"

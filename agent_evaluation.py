"""多步 Agent 任务评测：同时检查状态、工具轨迹、必要事实和引用。"""

from pydantic import BaseModel, ConfigDict, Field

from agent_graph import AgentGraphResponse, AgentGraphStatus


class AgentEvaluationCase(BaseModel):
    """人工定义的标准答案；不让被测 Agent 自己决定自己是否成功。"""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    expected_status: AgentGraphStatus = "COMPLETED"
    expected_tools: list[str] = Field(min_length=1)
    answer_must_contain: list[str] = Field(default_factory=list)
    answer_must_not_contain: list[str] = Field(default_factory=list)
    citation_required: bool = False
    return_application_expected: bool | None = None


class AgentEvaluationResult(BaseModel):
    """一条标准用例与一次 Agent 实际运行的评测结果。"""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    success: bool
    failure_reasons: list[str] = Field(default_factory=list)
    # 真实评测必须保留实际行为，否则失败后无法判断是路由、回答还是状态问题。
    actual_status: AgentGraphStatus | None = None
    actual_tools: list[str] = Field(default_factory=list)
    actual_answer: str | None = None
    duration_ms: float = Field(default=0.0, ge=0.0)
    model_requests: int = Field(default=0, ge=0)
    simulated_model_requests: int = Field(default=0, ge=0)
    model_http_attempts: int = Field(default=0, ge=0)
    model_retry_count: int = Field(default=0, ge=0)
    model_turn_durations_ms: list[float] = Field(default_factory=list)
    model_http_attempt_durations_ms: list[float] = Field(default_factory=list)
    # 批量运行时只保存异常类型，不保存可能含有业务数据或密钥的原始异常消息。
    error_type: str | None = None
    error_status_code: int | None = Field(default=None, ge=100, le=599)
    provider_error_code: str | None = Field(default=None, pattern=r"^[0-9]{1,8}$")
    return_application_created: bool = False


class AgentEvaluationSummary(BaseModel):
    """一批 Agent 评测结果的整体指标和逐条明细。"""

    model_config = ConfigDict(extra="forbid")

    total_cases: int = Field(ge=0)
    successful_cases: int = Field(ge=0)
    # failed 表示拿到了完整 Agent 响应，但状态、轨迹、事实或引用未满足标准。
    failed_cases: int = Field(ge=0)
    # errored 表示模型、网络或程序异常，根本没有可评分的完整 Agent 响应。
    errored_cases: int = Field(default=0, ge=0)
    # 端到端成功率以全部用例为分母，基础设施错误同样降低该指标。
    task_success_rate: float = Field(ge=0.0, le=1.0)
    # 完成率用于观察有多少用例真正得到可评分响应。
    evaluation_completion_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    # 已评分成功率排除运行错误，单独衡量 Agent 响应质量。
    scored_success_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    failure_counts: dict[str, int] = Field(default_factory=dict)
    results: list[AgentEvaluationResult] = Field(default_factory=list)


def is_task_successful(
    case: AgentEvaluationCase,
    response: AgentGraphResponse,
    *,
    return_application_created: bool | None = None,
) -> bool:
    """只有所有验收条件都满足时才返回 True。"""
    # 从每一步工具轨迹中只取工具名，并保留实际调用顺序。
    actual_tools = [step.tool_name for step in response.tool_trace]

    # 检查 Agent 最终状态是否符合这条评测用例的预期。
    status_matches = response.status == case.expected_status

    # 列表直接比较会同时检查工具名称、数量和调用顺序。
    tools_match = actual_tools == case.expected_tools

    # all() 只有在每段必要文字都出现在回答中时才返回 True。
    answer_matches = all(
        required_text in response.answer
        for required_text in case.answer_must_contain
    )
    forbidden_text_absent = all(
        forbidden_text not in response.answer
        for forbidden_text in case.answer_must_not_contain
    )

    # 不要求引用时直接通过；要求引用时必须出现统一的来源标记。
    citation_matches = (
        not case.citation_required
        or "来源：" in response.answer
    )

    application_created = (
        response.return_application is not None
        if return_application_created is None
        else return_application_created
    )
    return_application_matches = (
        case.return_application_expected is None
        or application_created == case.return_application_expected
    )

    # 四类验收条件必须全部成立，整条 Agent 任务才算成功。
    return (
        status_matches
        and tools_match
        and answer_matches
        and forbidden_text_absent
        and citation_matches
        and return_application_matches
    )


def collect_failure_reasons(
    case: AgentEvaluationCase,
    response: AgentGraphResponse,
    *,
    return_application_created: bool | None = None,
) -> list[str]:
    """返回一条 Agent 运行的全部失败原因；空列表表示没有失败。"""
    # 保留实际工具的调用顺序，用于和标准轨迹比较。
    actual_tools = [step.tool_name for step in response.tool_trace]

    # 同一次运行可能有多个问题，所以使用列表持续收集，不能遇到一个错误就 return。
    failure_reasons: list[str] = []

    # 状态错误时记录状态不匹配。
    if response.status != case.expected_status:
        failure_reasons.append("status_mismatch")

    # 工具名称、数量或顺序不同，都记录为工具轨迹不匹配。
    if actual_tools != case.expected_tools:
        failure_reasons.append("tool_sequence_mismatch")

    # 只要有一段必要文字缺失，就记录一次回答内容不完整。
    if not all(
        required_text in response.answer
        for required_text in case.answer_must_contain
    ):
        failure_reasons.append("missing_required_text")

    if any(
        forbidden_text in response.answer
        for forbidden_text in case.answer_must_not_contain
    ):
        failure_reasons.append("forbidden_answer_text")

    # 只有明确要求引用却没有来源标记时，才记录引用缺失。
    if case.citation_required and "来源：" not in response.answer:
        failure_reasons.append("missing_citation")

    application_created = (
        response.return_application is not None
        if return_application_created is None
        else return_application_created
    )
    if (
        case.return_application_expected is not None
        and application_created != case.return_application_expected
    ):
        failure_reasons.append("return_application_mismatch")

    return failure_reasons


def evaluate_response(
    case: AgentEvaluationCase,
    response: AgentGraphResponse,
    *,
    return_application_created: bool | None = None,
) -> AgentEvaluationResult:
    """评测一条 Agent 响应，并生成可保存、可聚合的结构化结果。"""
    # 先收集全部失败原因，避免只得到一个缺少诊断信息的布尔值。
    failure_reasons = collect_failure_reasons(
        case,
        response,
        return_application_created=return_application_created,
    )
    application_created = (
        response.return_application is not None
        if return_application_created is None
        else return_application_created
    )

    # 空列表在 Python 中是假值；没有失败原因就表示任务成功。
    return AgentEvaluationResult(
        case_id=case.case_id,
        success=not failure_reasons,
        failure_reasons=failure_reasons,
        actual_status=response.status,
        actual_tools=[step.tool_name for step in response.tool_trace],
        actual_answer=response.answer,
        model_requests=response.model_requests,
        simulated_model_requests=response.simulated_model_requests,
        model_http_attempts=response.model_http_attempts,
        model_retry_count=response.model_retry_count,
        model_turn_durations_ms=response.model_turn_durations_ms,
        model_http_attempt_durations_ms=response.model_http_attempt_durations_ms,
        return_application_created=application_created,
    )


def build_evaluation_summary(
    results: list[AgentEvaluationResult],
) -> AgentEvaluationSummary:
    """聚合逐条结果，计算任务成功率和失败原因分布。"""
    # 总用例数是成功率的分母。
    total_cases = len(results)

    # Python 中 True 可以按 1 参与求和，False 可以按 0 参与求和。
    successful_cases = sum(result.success for result in results)

    # 运行错误没有完整响应，不能伪装成 Agent 状态或回答不达标。
    errored_cases = sum(result.error_type is not None for result in results)

    # 只有拿到响应但没有通过标准的用例，才属于可评分失败。
    failed_cases = total_cases - successful_cases - errored_cases
    scored_cases = successful_cases + failed_cases

    # 一个失败任务可能贡献多个失败原因，所以需要两层循环分别计数。
    failure_counts: dict[str, int] = {}
    for result in results:
        for reason in result.failure_reasons:
            failure_counts[reason] = failure_counts.get(reason, 0) + 1

    # 空评测集没有可计算的成功率，这里约定返回 0.0，避免除以零。
    task_success_rate = (
        successful_cases / total_cases
        if total_cases > 0
        else 0.0
    )

    # 完成率下降通常提示模型、网络或程序可靠性问题。
    evaluation_completion_rate = (
        scored_cases / total_cases
        if total_cases > 0
        else 0.0
    )

    # 没有任何可评分响应时约定为 0.0，避免除以零和虚假的满分。
    scored_success_rate = (
        successful_cases / scored_cases
        if scored_cases > 0
        else 0.0
    )

    # 同时保留汇总指标和逐条明细，便于定位具体失败用例。
    return AgentEvaluationSummary(
        total_cases=total_cases,
        successful_cases=successful_cases,
        failed_cases=failed_cases,
        errored_cases=errored_cases,
        task_success_rate=task_success_rate,
        evaluation_completion_rate=evaluation_completion_rate,
        scored_success_rate=scored_success_rate,
        failure_counts=failure_counts,
        results=results,
    )

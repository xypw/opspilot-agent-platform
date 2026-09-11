"""比较条件一致的 Agent 评测报告，定位逐用例能力回退。"""

from pydantic import BaseModel, ConfigDict, Field

from evaluation_report import AgentEvaluationReport


class AgentEvaluationRegression(BaseModel):
    """一组可由 CI 直接判断的 Agent 回归结果。"""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    regressed_case_ids: list[str] = Field(default_factory=list)
    baseline_task_success_rate: float = Field(ge=0.0, le=1.0)
    current_task_success_rate: float = Field(ge=0.0, le=1.0)


def find_regressed_case_ids(
    baseline: AgentEvaluationReport,
    current: AgentEvaluationReport,
) -> list[str]:
    """返回基线成功、当前失败的用例编号。"""
    _ensure_comparable(baseline, current)
    current_results = {
        result.case_id: result
        for result in current.summary.results
    }
    return [
        baseline_result.case_id
        for baseline_result in baseline.summary.results
        if baseline_result.success
        and not current_results[baseline_result.case_id].success
    ]


def compare_evaluation_reports(
    baseline: AgentEvaluationReport,
    current: AgentEvaluationReport,
) -> AgentEvaluationRegression:
    """生成包含逐用例回归和总体成功率的门禁结果。"""
    regressed_case_ids = find_regressed_case_ids(baseline, current)
    return AgentEvaluationRegression(
        passed=not regressed_case_ids,
        regressed_case_ids=regressed_case_ids,
        baseline_task_success_rate=baseline.summary.task_success_rate,
        current_task_success_rate=current.summary.task_success_rate,
    )


def _ensure_comparable(
    baseline: AgentEvaluationReport,
    current: AgentEvaluationReport,
) -> None:
    """只有运行条件和用例集合一致时才允许比较。"""
    comparable_metadata = (
        baseline.schema_version,
        baseline.mode,
        baseline.runtime,
        baseline.cases_sha256,
    )
    current_metadata = (
        current.schema_version,
        current.mode,
        current.runtime,
        current.cases_sha256,
    )
    baseline_case_id_list = [result.case_id for result in baseline.summary.results]
    current_case_id_list = [result.case_id for result in current.summary.results]
    case_ids_are_unique = (
        len(baseline_case_id_list) == len(set(baseline_case_id_list))
        and len(current_case_id_list) == len(set(current_case_id_list))
    )
    if (
        comparable_metadata != current_metadata
        or set(baseline_case_id_list) != set(current_case_id_list)
        or not case_ids_are_unique
    ):
        raise ValueError("两份评测报告的运行条件或用例集合不一致。")

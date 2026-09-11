"""把 Agent 评测结果包装并保存成可复现的 JSON 报告。"""

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agent_evaluation import AgentEvaluationSummary
from agent_graph import AgentMode


EvaluationRuntime = Literal["isolated", "app"]


class AgentEvaluationReport(BaseModel):
    """评测摘要和本次运行环境共同组成一份可追溯报告。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.1"] = "1.1"
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    mode: AgentMode
    runtime: EvaluationRuntime
    cases_file: str = Field(min_length=1)
    cases_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    summary: AgentEvaluationSummary


def save_evaluation_report(
    report: AgentEvaluationReport,
    output_path: str | Path,
) -> Path:
    """以 UTF-8 JSON 保存报告，并返回最终文件路径。"""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return path


def calculate_cases_sha256(cases_path: str | Path) -> str:
    """计算评测集原始字节的 SHA-256 指纹。"""
    return sha256(Path(cases_path).read_bytes()).hexdigest()


def load_evaluation_report(report_path: str | Path) -> AgentEvaluationReport:
    """读取并校验一份已保存的评测报告。"""
    return AgentEvaluationReport.model_validate_json(
        Path(report_path).read_text(encoding="utf-8")
    )

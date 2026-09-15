"""事实核对契约：校验判定格式与引文出处，不把出处校验视为语义证明。"""

from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field


class SupportingQuote(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    chunk_id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class EvidenceVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    supported: bool
    supporting_quotes: list[SupportingQuote]
    missing_information: str


EvidenceReviewer = Callable[[str, list[dict]], dict]


def verify_evidence_review(raw_verdict: dict, candidates: list[dict]) -> tuple[EvidenceVerdict, list[dict]]:
    """拒绝矛盾判定与伪造引文，返回只包含核实摘录的证据副本。"""
    verdict = EvidenceVerdict.model_validate(raw_verdict)
    if not verdict.supported:
        if verdict.supporting_quotes or not verdict.missing_information.strip():
            raise ValueError("无法回答时必须说明缺失信息，且不能附带支持引文")
        return verdict, []
    if not verdict.supporting_quotes or verdict.missing_information.strip():
        raise ValueError("可以回答时必须提供支持引文，且不能同时声明信息缺失")

    by_id = {}
    for candidate in candidates:
        chunk_id = candidate.get("chunk_id")
        if not isinstance(chunk_id, str) or not chunk_id.strip() or chunk_id in by_id:
            raise ValueError("候选片段必须具有唯一的非空编号")
        by_id[chunk_id] = candidate

    selected = {}
    for quote in verdict.supporting_quotes:
        candidate = by_id.get(quote.chunk_id)
        if candidate is None:
            raise ValueError("支持引文引用了未知片段")
        content = candidate.get("content")
        if not quote.text.strip() or not isinstance(content, str) or quote.text not in content:
            raise ValueError("支持引文不是候选片段中的原文")
        excerpts = selected.setdefault(quote.chunk_id, [])
        if quote.text not in excerpts:
            excerpts.append(quote.text)

    return verdict, [
        {**by_id[chunk_id], "content": "\n".join(excerpts)}
        for chunk_id, excerpts in selected.items()
    ]

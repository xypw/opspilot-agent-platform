"""跨订单与政策问题的完成条件，以及可核对的最终回答。"""

from __future__ import annotations

import re

from grounding_policy import append_verified_citations
from order_service_client import OrderResponse


_ORDER_ID = re.compile(r"(?<![A-Za-z0-9_-])O-[0-9]{4}(?![A-Za-z0-9_-])")
_POLICY_TERMS = ("政策", "规则", "流程", "时效", "多久到账", "到账时间", "保修", "运费", "发票")
_REQUIRED = frozenset({"query_order", "search_knowledge_base"})


def required_tools(question: str) -> frozenset[str]:
    """只对明确的订单加政策问题设置双证据门槛；其他意图交给现有流程。"""
    if wants_return_application(question):
        return frozenset()
    if _ORDER_ID.search(question) and any(term in question for term in _POLICY_TERMS):
        return _REQUIRED
    return frozenset()


def wants_return_application(question: str) -> bool:
    """只有明确办理意图才允许 Agent 建草稿；资格/流程咨询保持只读。"""
    if any(term in question for term in (
        "流程", "怎么", "如何", "政策是什么", "政策有哪些", "规则是什么",
        "有什么规则", "能退", "可以退", "是否", "吗",
    )):
        return False
    return any(term in question for term in (
        "申请退货", "创建退货申请", "发起退货", "提交退货", "办理退货",
        "我要退货", "帮我退货",
    ))


def grounded_order_policy_answer(question: str, trace: list[dict],
                                 evidence: list[dict]) -> str:
    """模型负责选择工具；最终业务事实只来自已校验的订单与政策片段。"""
    order = next((step["result"] for step in trace
                  if step["tool_name"] == "query_order" and step["result"] is not None), None)
    if order is None or not evidence:
        raise ValueError("订单或政策证据缺失，不能生成联合回答")
    validated = OrderResponse.model_validate(order)
    excerpt = evidence[0].get("content")
    if not isinstance(excerpt, str) or not excerpt.strip():
        raise ValueError("政策证据缺少原文")
    answer = (
        f"订单 {validated.id}：商品 {validated.product}，当前状态 {validated.status}。\n"
        f"政策原文：{excerpt}"
    )
    if "到账" in question or "时效" in question:
        answer += "\n订单查询未提供退款审核通过时间，因此无法给出这笔订单的具体到账日期。"
    else:
        answer += "\n这条政策是否适用于该订单，仍需按对应业务规则核验。"
    return append_verified_citations(answer, evidence[:1])

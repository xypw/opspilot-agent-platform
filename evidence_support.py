"""检索后的确定性证据门禁。

第一版只识别“如何办理”类问题，容易把同一业务主题下答非所问的片段放行。
当前版本同时检查业务主题、问题需要的答案类型和精确业务标识符。它仍不是
通用语义蕴含模型，而是一道便宜、可解释、失败时默认拒绝的前置门禁。
"""

import re


_HOW_TO_QUESTION_V1 = re.compile(
    r"(?:怎么|如何|怎样|步骤|流程|操作).{0,16}(?:申请|办理|退款|退货|开票|发票)"
    r"|(?:申请|办理|退款|退货|开票|发票).{0,16}(?:怎么|如何|怎样|步骤|流程|操作)"
)
_HOW_TO_QUESTION = re.compile(
    r"(?:怎么|如何|怎样|步骤|流程|操作|在哪里|去哪).{0,16}(?:申请|办理|退款|退货|开票|发票|取消)"
    r"|(?:申请|办理|退款|退货|开票|发票|取消).{0,16}(?:怎么|如何|怎样|步骤|流程|操作|在哪里|去哪)"
)
_PROCEDURE_EVIDENCE_V1 = re.compile(
    r"点击|进入|打开|选择|填写|上传|登录|联系客服|联系人工"
    r"|(?:请|先|需要|应当).{0,12}提交(?:退款|申请|资料|表单)"
)
_PROCEDURE_EVIDENCE = re.compile(
    r"点击|进入|打开|选择|填写(?!的)|上传|登录|联系客服|联系人工"
    r"|(?:请|先|需要|应当).{0,12}提交(?:退款|申请|资料|表单)"
)
_OFFLINE_PROCEDURE_EVIDENCE = re.compile(
    r"(?:^|[。；：\n])\s*(?:请|先|须|需)?(?:携带|持)"
    r"[^。；\n]{1,30}(?:至|到|前往)[^。；\n]{1,20}"
    r"(?:办理|申请)(?:退款|退货)(?!后|之后)"
)

_TIMING_QUESTION = re.compile(r"多久|多长时间|什么时候|何时|几(?:个)?(?:分钟|小时|天|工作日|周|月)")
_TIMING_EVIDENCE = re.compile(
    r"(?:[0-9一二两三四五六七八九十百]+个?)?(?:分钟|小时|天|工作日|周|月)(?:内|后|以内)?"
    r"|立即|实时|当天|次日"
)
_DESTINATION_QUESTION = re.compile(r"(?:发送|发|退|到账|收到).{0,8}(?:哪里|哪儿|何处)|(?:哪里|哪儿|何处).{0,8}(?:发送|发|退|到账|收到)")
_DESTINATION_EVIDENCE = re.compile(r"发送至|发送到|发至|邮箱|账户|原支付渠道|原路|订单详情页|服务台")
_MEANING_QUESTION = re.compile(r"什么意思|表示什么|什么含义|含义是什么")
_MEANING_EVIDENCE = re.compile(r"表示|含义|指的是|说明")
_ELIGIBILITY_QUESTION = re.compile(
    r"能不能|是否可以|可以吗|还能|是否能|条件是什么|有什么条件|能.{0,8}吗|需要.{0,8}吗"
)
_ELIGIBILITY_EVIDENCE = re.compile(r"可以|可直接|不能|不可以|仅限|需要|需在|必须|条件")
_BUSINESS_IDENTIFIER = re.compile(
    r"(?<![a-z0-9_-])[a-z][a-z0-9]*(?:[_-][a-z0-9]+)+(?![a-z0-9_-])",
    re.IGNORECASE,
)

_TOPICS = {
    "refund": re.compile(r"退款|退钱|退回款|款项退回"),
    "return": re.compile(r"退货"),
    "invoice": re.compile(r"发票|开票"),
    "ticket": re.compile(r"工单"),
    "order_cancel": re.compile(r"(?:订单.{0,8}取消|取消.{0,8}订单)"),
    "payment": re.compile(r"支付|PAYMENT[_-]", re.IGNORECASE),
    "overtime": re.compile(r"加班|加班费|加班工资"),
    "membership": re.compile(r"会员|积分"),
    "logistics": re.compile(r"物流|快递|配送"),
    "warranty": re.compile(r"保修|维修"),
}
_QUALIFIER_GROUPS = (
    {
        "emergency": re.compile(r"紧急"),
        "standard": re.compile(r"普通"),
    },
    {
        "high": re.compile(r"高优先级"),
        "normal": re.compile(r"普通优先级|普通.{0,3}工单"),
    },
)


def _topics_in(text: str) -> set[str]:
    return {name for name, pattern in _TOPICS.items() if pattern.search(text)}


def _has_topic_match(query: str, content: str) -> bool:
    query_topics = _topics_in(query)
    return not query_topics or bool(query_topics & _topics_in(content))


def _has_identifier_match(query: str, content: str) -> bool:
    query_ids = {item.casefold() for item in _BUSINESS_IDENTIFIER.findall(query)}
    if not query_ids:
        return True
    content_ids = {item.casefold() for item in _BUSINESS_IDENTIFIER.findall(content)}
    return query_ids <= content_ids


def _has_qualifier_match(query: str, content: str) -> bool:
    """问题明确限定业务等级时，证据必须包含同一等级。"""
    for group in _QUALIFIER_GROUPS:
        query_labels = {name for name, pattern in group.items() if pattern.search(query)}
        if not query_labels:
            continue
        content_labels = {name for name, pattern in group.items() if pattern.search(content)}
        if not query_labels <= content_labels:
            return False
    return True


def _has_required_answer_type(query: str, content: str) -> bool:
    requirements = []
    if _HOW_TO_QUESTION.search(query):
        requirements.append(
            bool(_PROCEDURE_EVIDENCE.search(content) or _OFFLINE_PROCEDURE_EVIDENCE.search(content))
        )
    if _TIMING_QUESTION.search(query):
        requirements.append(bool(_TIMING_EVIDENCE.search(content)))
    if _DESTINATION_QUESTION.search(query):
        requirements.append(bool(_DESTINATION_EVIDENCE.search(content)))
    if _MEANING_QUESTION.search(query):
        requirements.append(bool(_MEANING_EVIDENCE.search(content)))
    if _ELIGIBILITY_QUESTION.search(query):
        requirements.append(bool(_ELIGIBILITY_EVIDENCE.search(content)))
    return all(requirements)


def filter_evidence_for_question_v1(query: str, evidence: list[dict]) -> list[dict]:
    """保留原始流程关键词策略，供离线报告做可复现基线对比。"""
    if not _HOW_TO_QUESTION_V1.search(query):
        return evidence
    return [
        item for item in evidence
        if (
            _PROCEDURE_EVIDENCE_V1.search(str(item.get("content", "")))
            or _OFFLINE_PROCEDURE_EVIDENCE.search(str(item.get("content", "")))
        )
    ]


def filter_evidence_for_question(query: str, evidence: list[dict]) -> list[dict]:
    """只保留主题、标识符和答案类型都满足当前问题的片段。"""
    return [
        item for item in evidence
        if _has_topic_match(query, str(item.get("content", "")))
        and _has_identifier_match(query, str(item.get("content", "")))
        and _has_qualifier_match(query, str(item.get("content", "")))
        and _has_required_answer_type(query, str(item.get("content", "")))
    ]
 

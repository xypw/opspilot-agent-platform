"""有界业务词表与事实类型驱动的证据门禁。

先确认问题的业务主题，再在同一业务语境内核对所需事实。规则不能识别的
表达返回 uncertain，留给语义审查；离线回答仅使用 admit。两条路径共用
同一判定，避免“未识别出条件”被空集合比较意外视为“条件全部满足”。
"""

from dataclasses import dataclass
import re
from typing import Literal


Decision = Literal["admit", "uncertain", "reject"]

# 保留最初基线，历史离线对照仍可复现。
_HOW_TO_QUESTION_V1 = re.compile(
    r"(?:怎么|如何|怎样|步骤|流程|操作).{0,16}(?:申请|办理|退款|退货|开票|发票)"
    r"|(?:申请|办理|退款|退货|开票|发票).{0,16}(?:怎么|如何|怎样|步骤|流程|操作)"
)
_PROCEDURE_EVIDENCE_V1 = re.compile(
    r"点击|进入|打开|选择|填写|上传|登录|联系客服|联系人工"
    r"|(?:请|先|需要|应当).{0,12}提交(?:退款|申请|资料|表单)"
)
_OFFLINE_PROCEDURE_EVIDENCE = re.compile(
    r"(?:^|[。；：\n])\s*(?:请|先|须|需)?(?:携带|持)"
    r"[^。；\n]{1,30}(?:至|到|前往)[^。；\n]{1,20}"
    r"(?:办理|申请)(?:退款|退货)(?!后|之后)"
)

# 这是售后业务词表，不读取案例编号、标签或文档标题来猜答案。
_TOPICS = {
    "refund": re.compile(
        r"退款|退钱|退回款|款项[^。；\n]{0,20}(?:退回|返还|退还|到账)"
        r"|钱[^。；\n]{0,12}(?:回到|退到|退回)"
    ),
    "return": re.compile(
        r"退货|退回商品|(?:商品|货品|东西|收货|签收|收到)[^。！？\n]{0,16}"
        r"(?:还能|可以|可否|能否|能|可).{0,2}退(?:吗|么|不|[？?])"
    ),
    "invoice": re.compile(r"发票|开票|票据|开具(?:结果|请求|申请)"),
    "ticket": re.compile(r"工单"),
    "order_cancel": re.compile(
        r"(?:订单|交易|待发货记录).{0,20}(?:取消|撤销|关闭)"
        r"|(?:取消|撤销|关闭).{0,20}(?:订单|交易)"
    ),
    "payment": re.compile(r"支付|PAYMENT[_-]", re.IGNORECASE),
    "overtime": re.compile(r"加班|加班费|加班工资"),
    "membership": re.compile(r"会员|积分"),
    "logistics": re.compile(r"物流|快递|配送"),
    "warranty": re.compile(r"保修|维修"),
}
_BUSINESS_IDENTIFIER = re.compile(
    r"(?<![a-z0-9_-])[a-z][a-z0-9]*(?:[_-][a-z0-9]+)+(?![a-z0-9_-])",
    re.IGNORECASE,
)
# 规则与语义审查共用服务端业务约定；不能由用户或检索文档修改。
TICKET_HIGH_PRIORITY_ALIASES = ("高优先级", "紧急")
# 工单域把“紧急级别”解释为高优先级；退款的紧急/普通等级单独约束。
_QUALIFIERS = {
    "ticket": {
        "high": re.compile("|".join(re.escape(term) for term in TICKET_HIGH_PRIORITY_ALIASES)),
        "normal": re.compile(r"普通"),
    },
    "refund": {
        "emergency": re.compile(r"紧急"),
        "standard": re.compile(r"普通"),
    },
}
_QUESTION_TYPES = {
    "procedure": re.compile(
        r"怎么(?!回事)|如何|怎样|步骤|流程|操作"
        r"|(?:在哪里|去哪|何处|哪里).{0,8}(?:申请|办理|取消|撤销)"
        r"|(?:申请|办理|取消|撤销).{0,8}(?:在哪里|去哪|何处|哪里)"
    ),
    "timing": re.compile(
        r"多久|多长时间|什么时候|何时|哪天|哪日|几(?:个)?(?:分钟|小时|天|日|工作日|周|月)"
    ),
    "destination": re.compile(
        r"(?:发|送|投递|退|返还|退还|到账|收到).{0,10}"
        r"(?:哪里|哪儿|何处|哪个账户|哪张卡|哪(?:[？?]|$))"
        r"|(?:哪里|哪儿|何处|哪个账户|哪张卡).{0,8}(?:发送|退回|到账|收到)"
    ),
    "meaning": re.compile(r"什么意思|表示什么|什么含义|含义是什么|怎么回事"),
    "eligibility": re.compile(
        r"能不能|是否可以|可以吗|还能|是否能|可否|能否|条件是什么|有什么条件"
        r"|能.{0,8}吗|需要.{0,8}吗|可以.{0,4}吗"
    ),
}
_PROCEDURE_EVIDENCE = re.compile(
    r"点击|进入|打开|选择|填写(?!的)|上传|登录|联系客服|联系人工"
    r"|(?:请|先|需要|应当).{0,12}提交(?:退款|申请|资料|表单)"
    r"|(?:在|从|通过)[^。；\n]{0,35}(?:入口|页面|列表|详情|记录)"
    r"[^。；\n]{0,20}(?:发起|申请|补全|补充|确认)"
)
_HISTORICAL_ACTION = re.compile(r"(?:已|已经)(?:发起|提交|受理|完成|打开|进入)")
_EVIDENCE_TYPES = {
    "timing": re.compile(
        r"[0-9一二两三四五六七八九十百]+个?(?:工作日|分钟|小时|天|日|周|月)"
        r"|立即|实时|当天|次日|\d{4}年\d{1,2}月\d{1,2}日"
    ),
    "destination": re.compile(
        r"(?:发送|发|送|投递).{0,4}(?:至|到).{0,18}(?:邮箱|信箱|账户|详情页)"
        r"|(?:退|返还|退还)(?:回|至|到).{0,12}(?:账户|银行卡|渠道)"
        r"|原路|沿原付款方式返还|退回原支付渠道"
    ),
    "meaning": re.compile(r"表示|含义|指的是|说明"),
    "eligibility": re.compile(r"可以|可直接|不能|不可以|仅限|需要|需在|必须|允许|有权|无权|不得|无需"),
}


@dataclass(frozen=True)
class EvidenceAssessment:
    decision: Decision
    reason: str
    required_types: tuple[str, ...] = ()
    provided_types: tuple[str, ...] = ()


def _topics_in(text: str) -> set[str]:
    topics = {name for name, pattern in _TOPICS.items() if pattern.search(text)}
    # “原支付账户”描述退款去向，不额外构成一项支付业务。
    if "refund" in topics and not re.search(r"PAYMENT[_-]", text, re.IGNORECASE):
        topics.discard("payment")
    return topics


def _required_answer_types(query: str) -> set[str]:
    required = {name for name, pattern in _QUESTION_TYPES.items() if pattern.search(query)}
    if "procedure" in required and re.search(r"(?:在哪里|去哪|哪里|何处).{0,8}(?:申请|办理)", query):
        if not re.search(r"退到|返还到|发到|发送到|到账", query):
            required.discard("destination")
    if not required and (re.search(r"政策|规则|制度|说明|指南", query) or any(
        pattern.fullmatch(query.strip(" ？?。")) for pattern in _TOPICS.values()
    )):
        required.add("overview")
    return required


def _provided_answer_types(content: str) -> set[str]:
    provided = {name for name, pattern in _EVIDENCE_TYPES.items() if pattern.search(content)}
    if not _HISTORICAL_ACTION.search(content) and (
        _PROCEDURE_EVIDENCE.search(content) or _OFFLINE_PROCEDURE_EVIDENCE.search(content)
    ):
        provided.add("procedure")
    return provided


def _qualifier_decision(query: str, content: str, topics: set[str]) -> str:
    uncertain = False
    for topic in topics:
        group = _QUALIFIERS.get(topic, {})
        required = {name for name, pattern in group.items() if pattern.search(query)}
        actual = {name for name, pattern in group.items() if pattern.search(content)}
        if required and not actual:
            uncertain = True
        elif not required <= actual:
            return "conflict"
    return "uncertain" if uncertain else "match"


def _scoped_units(content: str, query_topics: set[str]):
    """逗号后的主语/编号/等级切换开启新语境；省略主语的续句保留上下文。"""
    def labels(text: str):
        return {(topic, name) for topic in query_topics
                for name, pattern in _QUALIFIERS.get(topic, {}).items() if pattern.search(text)}

    for sentence in re.split(r"[。！？!?；;\n]+", content):
        current = ""
        current_topics, current_ids, current_labels = set(), set(), set()
        for clause in filter(str.strip, re.split(r"[，,]+", sentence)):
            topics = _topics_in(clause)
            ids = {item.casefold() for item in _BUSINESS_IDENTIFIER.findall(clause)}
            qualifiers = labels(clause)
            changed = current and any(
                previous and following and previous != following
                for previous, following in ((current_topics, topics), (current_ids, ids),
                                             (current_labels, qualifiers))
            )
            if changed:
                yield current
                current = ""
                current_topics, current_ids, current_labels = set(), set(), set()
            current = f"{current}，{clause}" if current else clause
            current_topics.update(topics)
            current_ids.update(ids)
            current_labels.update(qualifiers)
        if current:
            yield current


def assess_evidence_for_question(query: str, content: str) -> EvidenceAssessment:
    """只汇总匹配主题、编号和等级的语句，防止跨业务借用事实类型。"""
    topics = _topics_in(query)
    identifiers = {item.casefold() for item in _BUSINESS_IDENTIFIER.findall(query)}
    required = _required_answer_types(query)
    required_tuple = tuple(sorted(required))
    if not topics and not identifiers:
        return EvidenceAssessment("uncertain", "unknown_question_topic", required_tuple)
    content_ids = {item.casefold() for item in _BUSINESS_IDENTIFIER.findall(content)}
    if not identifiers <= content_ids:
        return EvidenceAssessment("reject", "identifier_conflict", required_tuple)
    if not required:
        return EvidenceAssessment("uncertain", "unknown_question_requirement")

    provided = set()
    matched_scope = False
    uncertain_scope = False
    for unit in _scoped_units(content, topics):
        unit_topics = _topics_in(unit)
        if topics and not topics <= unit_topics:
            if not unit_topics or bool(topics & unit_topics):
                uncertain_scope = True
            continue
        unit_ids = {item.casefold() for item in _BUSINESS_IDENTIFIER.findall(unit)}
        if not identifiers <= unit_ids:
            continue
        if unit_topics - topics and topics and not identifiers:
            # 多主题语句中的时间/资格可能属于另一个业务，留给语义审查。
            uncertain_scope = True
            continue
        qualifier = _qualifier_decision(query, unit, topics)
        if qualifier != "match":
            uncertain_scope |= qualifier == "uncertain"
            continue
        matched_scope = True
        provided.update(_provided_answer_types(unit))

    provided_tuple = tuple(sorted(provided))
    if matched_scope and (required <= provided or (required == {"overview"} and provided)):
        return EvidenceAssessment("admit", "matched_required_facts", required_tuple, provided_tuple)
    if not matched_scope:
        return EvidenceAssessment(
            "uncertain" if uncertain_scope else "reject",
            "ambiguous_scope" if uncertain_scope else "scope_conflict",
            required_tuple,
        )
    if provided and required.isdisjoint(provided) and not uncertain_scope:
        return EvidenceAssessment("reject", "answer_type_mismatch", required_tuple, provided_tuple)
    return EvidenceAssessment("uncertain", "missing_required_facts", required_tuple, provided_tuple)


def classify_evidence_for_question(query: str, content: str) -> Decision:
    return assess_evidence_for_question(query, content).decision


def partition_evidence_for_question(query: str, evidence: list[dict]) -> dict[str, list[dict]]:
    partition = {"admitted": [], "uncertain": [], "rejected": []}
    names = {"admit": "admitted", "uncertain": "uncertain", "reject": "rejected"}
    for item in evidence:
        decision = classify_evidence_for_question(query, str(item.get("content", "")))
        partition[names[decision]].append(item)
    return partition


def filter_evidence_for_question(query: str, evidence: list[dict]) -> list[dict]:
    """离线回答与真实模式使用同一准入判定；uncertain 不直接进入回答。"""
    return partition_evidence_for_question(query, evidence)["admitted"]


def filter_evidence_for_question_v1(query: str, evidence: list[dict]) -> list[dict]:
    if not _HOW_TO_QUESTION_V1.search(query):
        return evidence
    return [item for item in evidence if (
        _PROCEDURE_EVIDENCE_V1.search(str(item.get("content", "")))
        or _OFFLINE_PROCEDURE_EVIDENCE.search(str(item.get("content", "")))
    )]

"""不涉及业务事实的欢迎/界面帮助；不把问候送进政策检索，不批准申请。"""
import re


def simple_order_query(question: str) -> str | None:
    """只识别完整的单订单只读查询，不截取复合问题、申请或附带指令。"""
    match=re.fullmatch(
        r"(?:(?:请)?(?:帮我|给我)|请|麻烦)?\s*(?:查(?:询)?|查看)(?:一下)?\s*订单(?:号|编号)?"
        r"\s*[：:]?\s*([A-Za-z0-9_./-]{1,80})\s*(?:的?\s*(?:当前)?\s*(?:状态|信息|详情))?[？?。！!\s]*",
        question.strip())
    return match.group(1) if match else None


def service_guidance(question: str) -> str | None:
    text = question.strip().lower()
    order_id=simple_order_query(question)
    if order_id is not None and re.fullmatch(r"O-[0-9]{4}",order_id) is None:
        return (f"你提供的订单号“{order_id}”不符合当前系统的完整编号格式。"
                "请提供类似 O-7101 的完整订单号；我不会自动补前缀或补位。"
                "本次尚未查询订单，因此不能判断它是否存在。")
    if re.fullmatch(r"(你好|您好|嗨|hi|hello|在吗|早上好|下午好|晚上好)[呀啊呢吗！!？?。\s]*",text):
        return "你好，我是售后助手。你可以查询自己的订单、咨询退货政策，或上传商品图片。查询具体订单时请提供订单号。"
    if re.fullmatch(r"(查询订单怎么做|怎么查(?:询)?订单|如何查(?:询)?订单|订单怎么查(?:询)?)[？?。\s]*",text):
        return "请直接发送你的订单号，例如“查询订单 O-7101”。我会查询当前状态；也可以登录后进入“直接办理售后”页面。"
    if not re.search(r"O-[0-9]{4}",question) and re.fullmatch(
            r"(查(?:询)?订单|查看订单|我的订单|查(?:询)?物流|订单到哪了|我要退货|帮我申请退货|申请退货)[？?！!。\s]*",text):
        return "请提供你要查询或办理售后的订单号，例如 O-7101；收到后会核验订单归属和当前状态，提交申请前还需要你确认。"
    return None

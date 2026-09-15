"""只发起一次模型请求，观察工具调用；不执行工具，不生成工单结论。"""

import json
from pathlib import Path

import httpx
from dotenv import dotenv_values

from tool_schema import TOOLS

API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
MODEL = "glm-4.7-flash"  # 不允许环境配置自动替换为付费模型。
DEMO_QUESTION = "帮我查询工单 T-1003 的状态。"


class ModelAPIError(ValueError):
    """保存安全的状态码，让接口层区分上游错误，不解析错误文案。"""

    def __init__(self, status_code: int, provider_code: str = ""):
        self.status_code = status_code
        self.provider_code = provider_code
        suffix = f"，业务码 {provider_code}" if provider_code else ""
        super().__init__(f"模型请求失败（HTTP {status_code}{suffix}）；未重试或切换模型。")


def load_api_key() -> str:
    """仅程序读取本项目 .env；不打印密钥，也不使用其他供应商密钥。"""
    values = dotenv_values(Path(__file__).with_name(".env"), interpolate=False)
    api_key = (values.get("ZHIPU_API_KEY") or "").strip()
    if not api_key:
        raise ValueError("本地 .env 中未配置 ZHIPU_API_KEY；请在文件中填写，不要发到聊天。")
    return api_key


def build_initial_messages(question: str = DEMO_QUESTION) -> list[dict]:
    """每次创建新的消息列表，避免把上一次的查询混进当前对话。"""
    return [
        {
            "role": "system",
            "content": "你是企业知识、工单与订单助手。工单查询使用 query_ticket，订单查询使用 query_order。"
            "用户询问具体订单能否退货、七天无理由或退货期限时，必须使用 check_return_eligibility，"
            "不得自行计算签收天数或决定退货资格。"
            "通用的政策、流程和规范问题（例如‘我应该怎么退款’或‘退款多久到账’）"
            "必须先使用 search_knowledge_base，不需要订单编号；只能依据返回片段回答。"
            "用户要求修改工单优先级时使用 request_priority_change；该工具只创建待确认操作，"
            "必须提醒用户确认 action_id，不能声称已经修改。"
            "查询必须使用对应工具，不得编造编号、商品或状态；每次只查询一条记录。"
            "只有查询某个具体订单、工单或退货资格而用户未提供对应编号时，才先询问编号。"
            "收到工具结果后用中文简洁回答，"
            "仅依据工具结果；null 或空列表表示未找到对应信息。引用由程序根据标题和页码统一附加。",
        },
        {"role": "user", "content": question},
    ]


def request_message(
    api_key: str, client: httpx.Client, messages: list[dict], *, offer_tools: bool = True
) -> dict:
    """共用的单次请求脚手架：不重试、不跟随重定向、不回退模型。"""
    payload = {
        "model": MODEL,
        "messages": messages,
        "thinking": {"type": "disabled"},
        "max_tokens": 512,
        "stream": False,
    }
    if offer_tools:
        payload["tools"] = TOOLS
        payload["tool_choice"] = "auto"
    response = client.post(
        API_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json=payload,
        follow_redirects=False,
    )
    if response.status_code != 200:
        # 不回显响应正文或请求头，避免服务端错误信息泄露凭据。
        try:
            code = str(response.json()["error"]["code"])
        except (ValueError, KeyError, TypeError):
            code = ""
        # 只显示长度有限的数字业务码，不显示任意服务端文本。
        safe_code = code if code.isascii() and code.isdigit() and len(code) <= 8 else ""
        raise ModelAPIError(response.status_code, safe_code)
    try:
        message = response.json()["choices"][0]["message"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise ValueError("模型响应结构不符合预期。") from None
    if not isinstance(message, dict):
        raise ValueError("模型 message 应当是字典。")
    return message


def request_tool_call(api_key: str, client: httpx.Client) -> dict:
    """保留原来的单次工具选择预览入口。"""
    return request_message(api_key, client, build_initial_messages())


def extract_tool_preview(message: dict) -> dict:
    """检查工具名后展示参数文本；参数的解析、校验与执行留到下一步。"""
    calls = message.get("tool_calls")
    if not isinstance(calls, list) or len(calls) != 1:
        raise ValueError("模型没有返回恰好一个工具调用；本节不会自动执行或重试。")
    call = calls[0]
    function = call.get("function") if isinstance(call, dict) else None
    if not isinstance(function, dict):
        raise ValueError("模型返回了未允许的工具；不执行。")
    name = function.get("name")
    allowed_names = {tool["function"]["name"] for tool in TOOLS}
    if not isinstance(name, str) or name not in allowed_names:
        raise ValueError("模型返回了未允许的工具；不执行。")
    arguments = function.get("arguments")
    if not isinstance(arguments, str):
        raise ValueError("工具 arguments 应当是 JSON 格式的字符串。")
    return {"name": name, "arguments": arguments}


def main() -> int:
    try:
        api_key = load_api_key()
        with httpx.Client(timeout=httpx.Timeout(45, connect=10), trust_env=False) as client:
            message = request_tool_call(api_key, client)
        preview = extract_tool_preview(message)
    except httpx.HTTPError:
        print("模型请求出现网络或超时错误；未执行工具，未自动重试。")
        return 1
    except ValueError as error:
        print(str(error))  # 这里只输出本文件定义的安全错误信息。
        return 1
    print("模型：", MODEL)
    print("用户：", DEMO_QUESTION)
    print("真实模型返回的工具调用（尚未执行）：")
    print(json.dumps(preview, ensure_ascii=False, indent=2).replace(api_key, "[REDACTED]"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

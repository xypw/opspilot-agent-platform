"""Ollama原生协议适配；不下载模型、不跟随重定向、不执行返回动作。"""

import json
from uuid import uuid4

from model_runtime import ollama_configuration
from model_rate_limit import acquire_model_request
from tool_schema import TOOLS
from tool_policy import tool_is_enabled
from model_context import prepare_context, CONTEXT_WINDOW,ContextWindowRejected
from tool_args import (QueryOrderArgs,QueryTicketArgs,CheckReturnEligibilityArgs,
                       SearchKnowledgeBaseArgs,RequestPriorityChangeArgs)

ARGUMENT_MODELS={"query_order":QueryOrderArgs,"query_ticket":QueryTicketArgs,
                 "check_return_eligibility":CheckReturnEligibilityArgs,
                 "search_knowledge_base":SearchKnowledgeBaseArgs,
                 "request_priority_change":RequestPriorityChangeArgs}


class LocalModelFormatError(ValueError):
    """本地模型未给出有效结构，不是业务提交结果未知。"""


class LocalModelTruncatedError(LocalModelFormatError):
    """输出达到上限；不使用半截答案，可由证据层作一次有界格式恢复。"""


def native_messages(messages: list[dict]) -> list[dict]:
    converted, names = [], {}
    for item in messages:
        role = item.get("role")
        if role not in {"system", "user", "assistant", "tool"}:
            raise ValueError("不支持的模型消息角色")
        content = item.get("content") or ""
        if not isinstance(content, str):
            raise ValueError("本地文本消息必须为字符串")
        message = {"role":role, "content":content}
        calls = item.get("tool_calls")
        if calls:
            normalized = []
            for call in calls:
                function = call["function"]
                arguments = function["arguments"]
                arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
                if not isinstance(arguments, dict):
                    raise ValueError("工具参数必须是JSON对象")
                names[call["id"]] = function["name"]
                normalized.append({"function":{"name":function["name"],"arguments":arguments}})
            message["tool_calls"] = normalized
        if role == "tool":
            name = names.get(item.get("tool_call_id"))
            if not name:
                raise ValueError("工具结果没有匹配的已验证调用")
            message["tool_name"] = name
        converted.append(message)
    return converted


def ollama_chat(client, messages: list[dict], *, offer_tools: bool = False,
                json_output: bool = False, images: list[str] | None = None,
                output_schema: dict | None = None) -> dict:
    from preview_tool_call import ModelAPIError,extract_tool_preview
    base, model = ollama_configuration()
    if images and model != "qwen3-vl:4b":
        raise ValueError("当前本地模型不支持图片；未切换到云端")
    tools = [tool for tool in TOOLS if tool_is_enabled(tool["function"]["name"])] if offer_tools else []
    output_tokens = 4096 if json_output and not images else (2048 if images else 512)
    from model_runtime import setting
    from qwen_tokenizer import count_prompt
    counter=count_prompt if setting('QWEN_TOKENIZER_PATH') and model=='qwen3-vl:4b' else None
    prepared = prepare_context(messages, tools=tools, response_format=output_schema or ("json" if json_output else None),
                               output_tokens=output_tokens, image_count=len(images or []),token_counter=counter)
    converted = native_messages(prepared.messages)
    if images:
        converted[-1]["images"] = images
    payload = {"model":model,"messages":converted,"stream":False,"think":False,"truncate":False,"shift":False,
               "keep_alive":"5m", "options":{"temperature":0.1,"num_ctx":CONTEXT_WINDOW,
                   "num_predict":output_tokens}}
    if offer_tools:
        payload["tools"] = tools
    if json_output:
        payload["format"] = output_schema or "json"
    acquire_model_request()  # 本地也有共享入口，避免并发无界占用机器资源。
    # 流式读取设置硬上限，不能先将任意大响应全部读入内存。
    with client.stream("POST",base+"/api/chat",json=payload,follow_redirects=False) as response:
        if response.status_code != 200:
            if response.status_code==400:
                error_bytes=bytearray()
                for chunk in response.iter_bytes():
                    error_bytes.extend(chunk[:4096-len(error_bytes)])
                    if len(error_bytes)>=4096:break
                error_text=error_bytes.decode('utf-8',errors='ignore').lower()
                if any(term in error_text for term in ('prompt is longer than','context length','context window')):
                    raise ContextWindowRejected('文字或图片超过模型上下文窗口，请缩小图片或问题范围；未自动裁剪、重试或切换模型。')
            raise ModelAPIError(response.status_code)
        content = bytearray()
        for chunk in response.iter_bytes():
            content.extend(chunk)
            if len(content) > 256*1024:
                raise LocalModelFormatError("本地模型响应过大")
    try:
        result = json.loads(content)
        if not isinstance(result,dict):
            raise ValueError()
        if result.get("done_reason") == "length":
            error=LocalModelTruncatedError("本地模型输出达到上限，未使用截断内容")
            truncated_message=result.get('message')
            error.context_usage={'prompt_tokens':result.get('prompt_eval_count'),
                'output_tokens':result.get('eval_count'),'counted_input':prepared.estimated_input_units,
                'counter':prepared.estimator,
                'thinking_chars':len(str(truncated_message.get('thinking') or '')) if isinstance(truncated_message,dict) else 0}
            raise error
        if result.get("done") is not True:
            raise ValueError()
        message = result["message"]
        if not isinstance(message,dict) or message.get("role") != "assistant":
            raise ValueError()
        text=message.get("content")
        if text is not None and not isinstance(text,str):
            raise ValueError()
        normalized = {"role":"assistant","content":text or ""}
        if type(result.get('prompt_eval_count')) is int and type(result.get('eval_count')) is int:
            if min(result['prompt_eval_count'], result['eval_count']) < 0 or result['prompt_eval_count']+result['eval_count']>CONTEXT_WINDOW:
                raise LocalModelFormatError('模型报告上下文超限，未使用本轮输出')
            normalized['_context_usage']={'prompt_tokens':result['prompt_eval_count'],'output_tokens':result['eval_count'],
                'counted_input':prepared.estimated_input_units,'counter':prepared.estimator}
        calls=message.get("tool_calls")
        if calls is not None and not isinstance(calls,list):
            raise ValueError()
        if message.get("tool_calls"):
            if not offer_tools or not isinstance(message["tool_calls"],list):
                raise ValueError()
            normalized["tool_calls"] = []
            for call in message["tool_calls"]:
                if not isinstance(call,dict) or not isinstance(call.get("function"),dict):
                    raise ValueError()
                function = call["function"]
                if not isinstance(function["arguments"],dict) or not isinstance(function["name"],str):
                    raise ValueError()
                normalized["tool_calls"].append({
                    "id":"local_"+uuid4().hex,"type":"function",
                    "function":{"name":function["name"],
                                "arguments":json.dumps(function["arguments"],ensure_ascii=False)}})
            preview=extract_tool_preview(normalized)
            ARGUMENT_MODELS[preview["name"]].model_validate_json(preview["arguments"])
        elif not normalized["content"].strip():
            raise ValueError()
        return normalized
    except LocalModelTruncatedError:
        raise
    except (KeyError,TypeError,ValueError):
        raise LocalModelFormatError("本地模型响应格式无效；未执行任何返回指令") from None

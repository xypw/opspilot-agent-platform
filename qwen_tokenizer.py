"""Offline Qwen2 BPE and Ollama 0.40.5 Qwen3-VL non-thinking renderer.

Renderer behavior follows Ollama's MIT-licensed model/renderers/qwen3vl.go.
Only text is exact here; visual patch tokens are enforced by the backend with
truncate=false/shift=false rather than claiming base64 is tokenized text.
"""
import json
import hashlib
from functools import lru_cache
from pathlib import Path

from tokenizers import Tokenizer
from model_context import ContextBudgetExceeded


TOOLS_HEADER = '# Tools\n\nYou may call one or more functions to assist with the user query.\n\nYou are provided with function signatures within <tools></tools> XML tags:\n<tools>'
TOOLS_FOOTER = '\n</tools>\n\nFor each function call, return a json object with function name and arguments within <tool_call></tool_call> XML tags:\n<tool_call>\n{"name": <function-name>, "arguments": <args-json-object>}\n</tool_call><|im_end|>\n'
VERIFIED_OLLAMA_VERSIONS = {'0.40.5', '0.40.9'}


def _json(value):
    return json.dumps(value,ensure_ascii=False,separators=(', ',': ')).replace('&',r'\u0026').replace('<',r'\u003c').replace('>',r'\u003e').replace('\u2028',r'\u2028').replace('\u2029',r'\u2029')


def _property(value):
    # Mirror api.ToolProperty field order and omitempty behavior.
    result={}
    for key in ('anyOf','type','items','description','enum','properties','required'):
        if not value.get(key):continue
        item=value[key]
        if key=='properties':item={k:_property(v) for k,v in item.items()}
        if key=='anyOf':item=[_property(v) for v in item]
        result[key]=item
    return result


def _native_tool(tool):
    function=tool['function'];params=function.get('parameters',{})
    converted={'name':function['name']}
    if function.get('description'):converted['description']=function['description']
    parameters={'type':params.get('type','')}
    for key in ('$defs','items','required'):
        if params.get(key):parameters[key]=params[key]
    properties=params.get('properties')
    parameters['properties']={k:_property(v) for k,v in properties.items()} if properties is not None else None
    converted['parameters']=parameters
    return {'type':tool.get('type',''),'function':converted}


def render_qwen(messages,tools=None):
    """Render the supported non-thinking text subset; never invoke the model."""
    output=[]
    if tools:
        output.append('<|im_start|>system\n')
        if messages and messages[0]['role']=='system':output.append(messages[0]['content']+'\n\n')
        output.append(TOOLS_HEADER)
        for tool in tools:output.append('\n'+_json(_native_tool(tool)))
        output.append(TOOLS_FOOTER)
    elif messages and messages[0]['role']=='system':
        output.append('<|im_start|>system\n'+messages[0]['content']+'<|im_end|>\n')
    for i,row in enumerate(messages):
        role=row['role'];content=row.get('content') or ''
        if not isinstance(content,str):
            # Native visual messages are converted before this renderer.
            content=''.join(x.get('text','') for x in content if x.get('type')=='text')
        if role=='system' and i==0:continue
        if role in {'user','system','assistant'}:
            output.append('<|im_start|>'+role+'\n'+content)
            for j,call in enumerate(row.get('tool_calls') or []):
                function=call['function'];args=function['arguments']
                if isinstance(args,str):args=json.loads(args)
                if j or content:output.append('\n')
                output.append('<tool_call>\n{"name": "'+function['name']+'", "arguments": '+_json(args)+'}\n</tool_call>')
            if role!='assistant' or i!=len(messages)-1:output.append('<|im_end|>\n')
        elif role=='tool':
            if i==0 or messages[i-1]['role']!='tool':output.append('<|im_start|>user')
            output.append('\n<tool_response>\n'+content+'\n</tool_response>')
            if i==len(messages)-1 or messages[i+1]['role']!='tool':output.append('<|im_end|>\n')
        else:raise ValueError('不支持的上下文角色')
    if messages and messages[-1]['role']!='assistant':
        # qwen3-vl-thinking's factory does NOT set emitEmptyThinkOnNoThink.
        output.append('<|im_start|>assistant\n')
    return ''.join(output)


@lru_cache(maxsize=4)
def load_tokenizer(path):
    root=Path(path)
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    if not isinstance(manifest,dict):
        raise ValueError('分词资产清单必须为JSON对象')
    if (manifest.get('pre_tokenizer')!='qwen2' or manifest.get('renderer')!='qwen3-vl-thinking'
            or manifest.get('ollama_version') not in VERIFIED_OLLAMA_VERSIONS or manifest.get('model')!='qwen3-vl:4b'):
        raise ValueError('分词资产与当前模型不匹配')
    content=(root/'tokenizer.json').read_bytes()
    if hashlib.sha256(content).hexdigest()!=manifest.get('sha256'):
        raise ValueError('分词文件指纹不匹配，请重新导出')
    try:
        # Parse the exact bytes whose hash was checked. The native tokenizers
        # parser raises plain Exception for invalid schemas, not ValueError.
        return Tokenizer.from_str(content.decode('utf-8'))
    except Exception:
        raise ValueError('分词文件格式无效，请重新导出') from None


def count_prompt(messages,tools=None):
    from model_runtime import setting
    root=setting('QWEN_TOKENIZER_PATH')
    if not root:return None
    try:
        tokenizer=load_tokenizer(root)
        return len(tokenizer.encode(render_qwen(messages,tools),add_special_tokens=False).ids)
    except (OSError,ValueError):
        raise ContextBudgetExceeded('本地分词资产缺失、指纹或版本不匹配，请重新导出并校验；未发送模型请求。') from None

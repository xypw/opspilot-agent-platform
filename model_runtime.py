"""模型位置由服务端配置；用户请求不能指定地址、模型或供应商凭据。"""

import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values


def setting(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value is None:
        value = dotenv_values(Path(__file__).with_name(".env"), interpolate=False).get(name, default)
    return str(value or "").strip()


def model_provider() -> str:
    provider = setting("OPSPILOT_MODEL_PROVIDER", "zhipu")
    if provider not in {"ollama", "zhipu"}:
        raise ValueError("模型服务配置无效；未自动切换供应商")
    return provider


def ollama_configuration() -> tuple[str, str]:
    base = setting("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    url = urlsplit(base)
    if (url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost", "::1", "host.docker.internal"}
            or url.port != 11434 or url.username or url.password or url.path or url.query or url.fragment):
        raise ValueError("Ollama只允许配置本机11434端口，不接受外部地址或任意路径")
    model = setting("OLLAMA_MODEL", "qwen3-vl:4b")
    if model not in {"qwen3-vl:4b", "qwen3:4b"}:
        raise ValueError("未配置受支持的本地模型；不会下载或切换云端模型")
    return base, model


def public_runtime() -> dict:
    local = model_provider() == "ollama"
    vision = local and ollama_configuration()[1] == "qwen3-vl:4b"
    return {"location": "local" if local else "cloud", "requires_image_consent": not local,
            "vision_available": vision if local else setting("VISION_ENABLED", "false").lower() == "true",
            "notice": ("当前使用本机模型，问题和图片不发送到云端模型。"
                       if local else "当前使用云端模型；图片及相关对话须经本轮授权后发送。")}

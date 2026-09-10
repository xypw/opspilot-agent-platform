"""根据显式配置选择订单查询后端。"""

import os
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from dotenv import dotenv_values

from order_service_client import JavaOrderClient
from return_review_client import review_return, ReasonCode


OrderQuery = Callable[[str], dict[str, object] | None]
ReturnEligibilityQuery = Callable[[str], dict[str, object] | None]


def _load_local_values() -> dict:
    """只读取项目本地 .env，不打印其中可能存在的密钥。"""
    return dotenv_values(Path(__file__).with_name(".env"), interpolate=False)


def load_order_query_backend() -> str:
    """环境变量优先；默认 memory，避免测试意外连接外部服务。"""
    values = _load_local_values()
    return (
        os.getenv("ORDER_QUERY_BACKEND")
        or values.get("ORDER_QUERY_BACKEND")
        or "memory"
    ).strip().lower()


def load_order_service_url() -> str:
    """Java 地址由部署配置决定，绝不接受模型提供的 URL。"""
    values = _load_local_values()
    return (
        os.getenv("ORDER_SERVICE_URL")
        or values.get("ORDER_SERVICE_URL")
        or "http://127.0.0.1:8081"
    ).strip()


class ConfiguredJavaOrderQuery:
    """每次调用都完整关闭 HTTP 连接；后续再优化为应用级连接池。"""

    def __init__(self, service_url: str):
        parsed = urlsplit(service_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("ORDER_SERVICE_URL 必须是无凭据、无路径的 HTTP(S) 服务地址。")
        self.service_url = service_url.rstrip("/")

    def __call__(self, order_id: str) -> dict[str, object] | None:
        # 地址、超时和重定向策略都由程序控制，模型只提供合法订单号。
        with httpx.Client(
            base_url=self.service_url,
            timeout=httpx.Timeout(2.0),
            trust_env=False,
            follow_redirects=False,
        ) as http:
            return JavaOrderClient(http).get_by_id(order_id)

    def check_return_eligibility(self, order_id: str) -> dict[str, object] | None:
        """调用同一 Java 服务的规则接口，不在 Python 中复制业务判断。"""
        with httpx.Client(
            base_url=self.service_url,
            timeout=httpx.Timeout(2.0),
            trust_env=False,
            follow_redirects=False,
        ) as http:
            return JavaOrderClient(http).get_return_eligibility(order_id)

    def review_return(self, order_id: str, reason: str, code: ReasonCode) -> dict:
        with httpx.Client(base_url=self.service_url, timeout=httpx.Timeout(2.0),
                          trust_env=False, follow_redirects=False) as http:
            return review_return(http, order_id, reason, code)


def build_order_query(
    backend: str | None = None,
    service_url: str | None = None,
) -> OrderQuery | None:
    """memory 返回 None 表示保留默认实现；java 返回 HTTP 查询。"""
    selected_backend = load_order_query_backend() if backend is None else backend.strip().lower()
    if selected_backend == "memory":
        return None
    if selected_backend == "java":
        selected_url = load_order_service_url() if service_url is None else service_url.strip()
        return ConfiguredJavaOrderQuery(selected_url)
    raise ValueError("ORDER_QUERY_BACKEND 只能是 memory 或 java。")


def build_return_eligibility_query(
    service_url: str | None = None,
) -> ReturnEligibilityQuery:
    """退货规则只存在于 Java，因此该工具始终指向配置好的 Java 服务。"""
    selected_url = load_order_service_url() if service_url is None else service_url.strip()
    return ConfiguredJavaOrderQuery(selected_url).check_return_eligibility


def build_return_review():
    return ConfiguredJavaOrderQuery(load_order_service_url()).review_return

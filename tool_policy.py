"""服务端工具启用策略；旧工单演示没有用户归属契约，只能用于离线教学。"""

from auth import auth_required


LEGACY_TICKET_TOOLS = frozenset({"query_ticket", "request_priority_change"})


class ToolDisabledError(PermissionError):
    """部署模式禁止调用没有权限边界的旧教学工具。"""


def tool_is_enabled(tool_name: str) -> bool:
    return tool_name not in LEGACY_TICKET_TOOLS or not auth_required()


def require_tool_enabled(tool_name: str) -> None:
    if not tool_is_enabled(tool_name):
        raise ToolDisabledError("启用鉴权时已停用旧工单工具，该流程不可查询或执行。")

"""离线演示人工确认：申请阶段不修改，确认阶段才执行一次。"""

import json

from pending_actions import PendingActionStore
from tickets import change_ticket_priority, query_ticket


def run_demo() -> dict:
    store = PendingActionStore(id_factory=lambda: "act_demo_001")
    original_priority = query_ticket("T-1003")["priority"]
    try:
        pending = store.propose_priority_change("T-1003", "high")
        priority_before_confirmation = query_ticket("T-1003")["priority"]
        executed = store.confirm(pending.action_id)
        priority_after_confirmation = query_ticket("T-1003")["priority"]
        repeated = store.confirm(pending.action_id)
        return {
            "pending_action": pending.model_dump(),
            "priority_before_confirmation": priority_before_confirmation,
            "executed_action": executed.model_dump(),
            "priority_after_confirmation": priority_after_confirmation,
            "repeated_confirmation_status": repeated.status,
        }
    finally:
        # 演示结束恢复虚构数据，避免影响其他练习。
        change_ticket_priority("T-1003", original_priority)


if __name__ == "__main__":
    print("人工确认离线演示；未调用模型、未读取密钥：")
    print(json.dumps(run_demo(), ensure_ascii=False, indent=2))

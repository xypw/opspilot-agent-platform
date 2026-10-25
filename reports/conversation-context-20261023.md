# 2026-10-23 上下文管理本地完成记录

## 本轮范围

补齐门户长对话管理，不新增数据库组件、外部模型、依赖下载或业务写入。保留此前侧边聊天的接线和等待恢复改动；不操作原用户申请。

## 实现

- `conversation_context.py`：最近三轮、当前会话旧文按需检索、带消息来源的确定性用户原文摘要。历史 JSON 默认最多 6000 UTF-8 字节；存在旧记录时为检索/摘要保留预算，避免长闲聊占满。来源兼容 PostgreSQL UUID。
- `model_context.py`：模型请求统一估算预算，计入提示、工具 schema、输出格式与输出预留；裁剪只作用于明确的可选历史，按摘要/近期/相关旧文优先级移除。当前问题、策略、工具调用结果和证据要么完整保留，要么明确拒绝。
- `agent_graph.py`、`vision_service.py`：历史只作为 user 级不可信参考，不将历史助手回答提升为授权或事实；图片保持直接视觉输入，不引入 OCR 替代。
- `preview_tool_call.py`、`ollama_gateway.py`：文字、证据和视觉的预算入口都在共享准入及 HTTP 前。裁剪只改变本次传给模型的副本，不改原历史和检查点中的业务事实。
- `retry_policy.py`：预算拒绝不重试、不计 HTTP 尝试。
- `portal_api.py`、`main.py`：预算拒绝明确返回 422；门户记录为 FAILED 并释放活动标记，不误认为未知业务写入。

摘要按需重建，无新表或迁移；正式订单、草稿、金额、有效期及确认快照继续依赖服务端状态和 Java 校验，不能从摘要恢复权限或批准。

## 验证

在隔离内存配置中运行：

```powershell
$env:OPSPILOT_AUTH_REQUIRED='false'
$env:OPSPILOT_DEBUG_ENABLED='true'
$env:REDIS_URL=' '
$env:TICKET_REPOSITORY_BACKEND='memory'
$env:ORDER_QUERY_BACKEND='memory'
.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_model_context tests.test_conversation_context tests.test_customer_portal tests.test_ollama_gateway tests.test_evidence_format_retry tests.test_agent_graph tests.test_simple_order_queries tests.test_auth_boundary tests.test_runtime_boundaries tests.test_review_boundary_regressions tests.test_retry_policy
node --test static/portal.test.cjs static/service.test.cjs static/demo.test.cjs
```

结果：Python **153 项通过**；Node **29 项通过**；`git diff --check` 通过（仅 CRLF 提示）。这是相关定向回归，不是全仓库或真实模型效果评测。

覆盖：长历史旧凭证召回、近期记录预算竞争、显式不同订单、完整否定条件摘录、消息来源、提示注入私有字段隔离、当前问题不截断、完整工具配对、文字/视觉/证据超限零 HTTP、零配额、零重试、失败释放活动标记、历史确认不自动执行，以及原页面恢复/确认边界。

## 未验证及限制

- 预算采用 UTF-8 字节及协议/图片预留估算，不是对应模型 tokenizer 的精确计数；可能提前拒绝较长中文和证据，不能保证任意图片不超供应商窗口。
- 抽取摘要不保证无损；旧历史检索是关键词/订单号检索，不是长期语义记忆。
- 门户仍最多 100 轮；算法超过 100 轮的合成测试不代表放开产品上限。
- 本轮未调用本地或云端模型，未修改历史评测数字，未部署、提交或推送。此前 Docker 启动故障尚未在本轮复验；容器和真实模型验收留待恢复后另行执行。

完整机制见 [上下文说明](../docs/conversation-context.md)。

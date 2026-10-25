# 2026-10-22 门户原请求只读恢复

## 根因

前端以客户端 `request_id` 在本人的会话结果中识别原请求，生产 `public_turn()` 却同时删除了 `request_id` 和私有 `request_hash`。此前前端替身保留了该字段，漏测了真实契约。此外，轮询读到终态只更新提示，并未结束对原 POST 连接的等待。

## 本轮改动

- 本人会话返回 `request_id`，继续隐藏 `request_hash`；归属鉴权不变。API 替身复用生产序列化，真实 PG 用例也断言关联字段。
- 原 POST 与只读轮询结果竞争；已读到持久结果即显示，不等待旧连接。关闭浏览器传输不等于取消服务端工作流。
- 断网或超时保留关联，提供“检查原请求进度”按钮；结果未知时再次点击发送也只查询，不重放原消息。
- 已收到结果后先渲染结果及待确认快照，再增强刷新历史；刷新断网不能把已知结果降级为未发送。增强刷新每次 GET 最多 8 秒。
- 登录失效清除旧会话与等待；终态或失败后清理轮询。待确认按钮仍须勾选，恢复不会自动确认。

## 已完成验证

- Python 相关回归：96 项通过，包含生产序列化与 API 返回契约。
- Node 三组界面回归：29 项通过；覆盖原 POST 不返回、四种恢复状态、断网按钮、历史刷新失败/悬挂、登录失效。
- 真实浏览器静态页面、合成接口：6 个场景通过（完成、待确认、失败、结果未知、登录失效、历史断网）。每场景只有一次消息 POST、零次确认 POST。
- 本轮未调用本地或云端模型，未重发、确认或修改原用户申请；未改上下文编排代码。

## 部署状态

未完成：真实 PostgreSQL 集成与 API 重建验收。Docker Desktop 初次启动遇到 Secrets Engine `engine.sock` 文件访问错误。用户授权一次退出重启；正常退出超时后使用官方强制退出，再启动仍失败，此次为 Ingest 服务 `sailor-ingest.sock` 文件无法访问。已停止部署，没有重置、删文件、动数据卷或更新应用镜像。需先恢复 Docker 引擎。不得把上述合成浏览器验证宣称为部署环境或真实模型效果验证。

## 复现

在隔离的环境配置中运行：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_portal_response_contract tests.test_customer_portal tests.test_simple_order_queries tests.test_agent_graph tests.test_auth_boundary tests.test_review_boundary_regressions tests.test_runtime_boundaries
node --test static/portal.test.cjs static/service.test.cjs static/demo.test.cjs
```

PG 验收显式设置 `RUN_PORTAL_POSTGRES_INTEGRATION=1`，使用每例随机 schema；不操作原有订单和用户数据。

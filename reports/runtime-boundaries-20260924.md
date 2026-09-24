# 2026-09-24 运行边界验证

## 结果

| 范围 | 结果 | 能支持的结论 |
| --- | --- | --- |
| 限流、检查点、业务与 API 相关测试 | 62/62，无跳过 | 当前所覆盖的契约通过 |
| 其中真实本地 Redis 集成 | 4/4 | 跨客户端、跨进程原子计数，实际过期恢复，无 TTL 计数保守处理 |
| 证据审查及评测器相关测试 | 45/45 | 审查适配器、记录及评测逻辑未在覆盖范围内回归 |
| 固定离线模拟任务 | 8/8 | 固定流程及业务约束通过，不代表真实模型成功率 |

两进程测试共享同一个 Redis key，限额 3，共执行 8 次准入尝试，3 次放行、5 次拒绝。该测试未请求模型，未执行业务写入；仅写入 UUID 命名的测试计数并在完成后删除。

## 复现

先启动已有 Redis 环境（本次测试地址 127.0.0.1:6380），在项目目录执行：

```powershell
$env:REDIS_URL=' '
$env:TICKET_REPOSITORY_BACKEND='memory'
$env:KNOWLEDGE_STORE_BACKEND='memory'
$env:ORDER_QUERY_BACKEND='memory'
$env:OPSPILOT_AUTH_REQUIRED='false'
$env:MODEL_RATE_LIMIT_REQUESTS='0'
$env:RUN_REDIS_RATE_LIMIT_INTEGRATION='1'
.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_langgraph_checkpointer tests.test_runtime_boundaries tests.test_model_rate_limit tests.test_retry_policy tests.test_agent_graph tests.test_auth_boundary tests.test_service_api -q
.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_evidence_review tests.test_agent_evaluation_runner tests.test_agent_evaluation tests.test_run_agent_evaluation_script -q
.\.venv\Scripts\python.exe -X utf8 scripts/run_agent_evaluation.py --mode mock --runtime isolated
```

限流集成测试自行创建 Redis 客户端，不使用应用的 `REDIS_URL`；可通过 `RATE_LIMIT_TEST_REDIS_URL` 指定测试实例。关闭应用限流只为隔离其他 MockTransport 测试，不会禁用集成测试直接创建的限流器。

## 当时验证范围与后续部署

以下限制描述本报告首次源码验证时的状态。后续已经完成应用部署与离线回归，见 [本机部署验收](deployment-20260924.md)，不要将下面的历史状态当作当前部署状态。

- 首次检查时 Redis 端口超时，之后确认现有容器已运行再复测通过；没有由本轮启动或重建容器。
- 未运行全量回归或外部模型请求，没有新吞吐量、响应时间或供应商限流改善指标。
- 当前运行容器仍需另行部署新代码；本文只证明源码和本地依赖集成验证。
- 原始 unittest 本地日志为 `reports/redis-boundaries-20260924.log`，仓库保留本摘要、测试源码及固定任务 JSON 报告。

# 工具授权与证据链路修复验证（2026-10-05）

## 本轮修复

- 部署鉴权开启时，停用尚无用户归属契约的旧工单查询与优先级修改工具；模型工具列表、返回工具名校验、执行入口及旧检查点读取/恢复使用同一策略。提示词同步说明功能不可用。关闭鉴权的离线演示保留原工单案例。
- 普通知识问答从已核验的证据摘录生成正文与引用，避免给模型编造的事实附上真实页码；这不证明原始资料或语义审查在所有问题上都正确。
- 真实证据审查接口的充分性判定绑定完整引文集合；集合超过返回上限时返回证据不足，不再裁剪后沿用原判定。本轮通过替身审查器验证，无外部模型调用。
- 回归 Skill、README 与 CI 同步隔离本地鉴权、数据库、限流和外部重排配置；鉴权测试在用例中显式重新启用，不修改生产默认值。

## 已执行验证

| 检查 | 结果与范围 |
| --- | --- |
| Python 测试 | 508 项：501 通过、7 跳过、0 失败 |
| 本轮边界回归 | `tests/test_review_boundary_regressions.py` 15 项通过，包含提示词/工具列表、强制工具调用、旧检查点、最终答案和证据裁剪 |
| 页面脚本 | `static/demo.test.cjs`、`static/service.test.cjs`，4 项通过 |
| 固定模拟流程 | 8/8 通过，`mode=mock`、`runtime=isolated`，模型 HTTP 请求为 0 |
| 回归门禁 | 对比 `agent-v2-offline-20260923.json` 通过，无退化案例 |

7 项跳过是显式关闭的 Redis 检查点、Redis 共享限流及 PostgreSQL 工单仓库集成测试。本轮未启动 Docker，未复测真实模型、外部供应商、Java 服务部署和跨服务联调。模拟流程中的旧工单案例只代表关闭鉴权的隔离环境；部署模式禁用行为由鉴权回归用例验证。

逐例数据：[review-fixes-20261005.json](review-fixes-20261005.json)。这些结果是软件行为回归，不是模型准确率或生产可靠性保证。

## 复现

在专用测试终端中按 [回归 Skill](../.codex/skills/opspilot-regression/SKILL.md) 设置全部隔离环境变量，再运行：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests
node --test static/demo.test.cjs static/service.test.cjs
.\.venv\Scripts\python.exe -X utf8 scripts/run_agent_evaluation.py --mode mock --runtime isolated --output output/evaluation/current-agent-evaluation.json
.\.venv\Scripts\python.exe -X utf8 scripts/check_agent_regression.py --baseline reports/agent-v2-offline-20260923.json --current output/evaluation/current-agent-evaluation.json
```

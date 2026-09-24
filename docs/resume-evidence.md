# OpsPilot 简历证据索引

当前项目定位为“订单感知售后 Agent + RAG + Java 业务服务”。Agent 负责多步查询订单与政策证据，并在缺证据时拒答；固定办理步骤仍可由用户直达。已有的 7 条模拟任务和真实模型结果来自旧任务集，不能直接当作新增联合回答门槛的准确率。新 `/service` 入口的 API、页面与 Java 服务已分别回归，并完成本机跨服务冒烟。

| 简历表述 | 可复现证据 | 边界 |
| --- | --- | --- |
| 47 条固定证据回归中误放行由 21 条降至 0，误拒为 0 | `evaluation_data/evidence_sufficiency_cases.json`、`evaluation_data/evidence_sufficiency_challenge_cases.json`、`reports/rag-offline-evidence-v2.json` | 案例参与规则开发，不是独立线上样本 |
| 旧 20 例诊断集误拒由 7 降至 0，误放行由 1 降至 0 | `reports/evidence-holdout-rules.json`、`reports/evidence-v3-diagnostic20-verified.json` | 已用于修复，现为回归集；不能当作未见集泛化准确率 |
| 99 条固定证据回归通过 | `tests/test_evidence_policy_regression.py`；原 47 例、旧 20 例及新增 32 例；`reports/evidence-v3-boundary32-verified.json` | 新增 32 例的标签在实现修改前固定，同一任务编制，不是外部独立评测 |
| 四服务模拟模型固定任务 7/7 | `reports/agent-evaluation-app-mock-20260913.json` | 验证跨服务控制流，不代表真实模型质量 |
| Agent 联合问题先查订单再检索政策，缺任一证据拒答，最终回答不采用模型编造的到账日 | `agent_task_contract.py`、`tests/test_agent_graph.py` 中联合问题、提前回答和虚构到账日测试 | 固定模型回放证明工程约束；尚无新门槛下的真实模型成功率 |
| Agent v2 固定任务 8/8；资格咨询不建草稿，明确申请等待确认 | `evaluation_data/agent_task_cases_v2.json`、`reports/agent-v2-offline-20260923.json` | 离线模拟模型与虚构数据；旧 7 条报告对应 v1，不得当作真实模型准确率 |
| 2026-09-23 新证据审查单例真实模型冒烟被限流 | `reports/evidence-live-smoke-20260923.json` 及同名 JSONL | 1 条虚构案例，3 次 HTTP 尝试均为 429；无有效语义结果，不得计入模型准确率 |
| 确认前业务写入违规为 0 | 同上报告中的 Java 权威副作用探针 | 仅覆盖固定 7 条案例 |
| 真实模型评测发现限流与只读工具漏调 | `reports/agent-evaluation-live-isolated-20260913.json` | 实际结果为 4/7，不能写成真实模型 7/7 |
| Reranker 对照无可测增益 | `reports/retrieval-reranker-20260915.json` | 11 条正例过于简单，不支持提升结论 |
| Python 回归共 430 项，427 通过、3 项跳过 | README 中的隔离命令；`reports/evidence-v3-python-final.log` | 含 99 条证据子用例、新入口 API 与 Agent 完成门槛；不等同业务效果 |
| 新入口页面 1 项交互测试与 Java 服务 34 项测试 | `static/service.test.cjs`、`tests/test_service_api.py`、`business-service/src/test/`；2026-09-23 本地结果 | 页面测试用模拟 HTTP；Java 测试有 2 项跳过，未证明跨服务部署成功 |
| 本机 Python→Java 显式售后链路通过 | `scripts/smoke_service_java.py`；2026-09-23 本地 Java 内存服务冒烟 | 覆盖订单、资格、草稿、409 和确认；鉴权关闭，仅证明本地接口契约，不证明生产部署或模型质量 |
| 20 条改写集的真实证据审查：充分证据接纳 7/10，有效且正确 15/20 | `reports/evidence-holdout-live-review-20260919.json` 及同名 JSONL；`reports/2026-09-19-evidence-review.md` | 1 条语义误拒和 4 条运行失败；不能写成 17/20 正确或零误拒，不能声称完整 RAG 或 Agent 端到端成功率 |

任何简历数字调整前先重跑对应脚本并保存原始报告，禁止把 mock、规则基线或单元测试通过率改写成真实模型准确率。

## 2026-09-24 补充验证

- Redis 共享模型准入：62 项针对性检查全部通过，其中 4 项连接现有本地 Redis。两个独立 Python 进程共发起 8 次准入请求，配额为 3 时仅 3 次放行；验证真实过期恢复及拒绝不续期。见 `tests/test_model_rate_limit.py`、`reports/redis-boundaries-20260924.log`。这是准入一致性测试，不是压力测试、请求延迟优化或消除供应商 429 的证据。
- 另有 45 项证据审查及评测器相关检查通过；8 条固定模拟任务复测仍为 8/8，见 `reports/agent-v2-offline-20260924.json`。没有新增真实模型质量指标。
- 前轮 Java PostgreSQL 复测为 34/34，无跳过，包含并发确认及服务对象重建两项集成验证；不代表真实进程故障恢复，未在本轮重复执行。原始本机报告为简历目录中的 `PostgreSQL复测结果-2026-09-24.md`。
- Redis 限流及 TTL 配置已部署到本机 API；更新后在容器内通过 4 项 Redis 集成验证，并验证重试和证据审查共享配额。见 [`deployment-20260924.md`](../reports/deployment-20260924.md)。全量离线回归为 442 通过、7 跳过，页面测试 4/4；架构取舍与局限见 `architecture-decisions.md`。

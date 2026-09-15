# OpsPilot 简历证据索引

| 简历表述 | 可复现证据 | 边界 |
| --- | --- | --- |
| 47 条固定证据回归中误放行由 21 条降至 0，误拒为 0 | `evaluation_data/evidence_sufficiency_cases.json`、`evaluation_data/evidence_sufficiency_challenge_cases.json`、`reports/rag-offline-evidence-v2.json` | 案例参与规则开发，不是独立线上样本 |
| 四服务模拟模型固定任务 7/7 | `reports/agent-evaluation-app-mock-20260913.json` | 验证跨服务控制流，不代表真实模型质量 |
| 确认前业务写入违规为 0 | 同上报告中的 Java 权威副作用探针 | 仅覆盖固定 7 条案例 |
| 真实模型评测发现限流与只读工具漏调 | `reports/agent-evaluation-live-isolated-20260913.json` | 实际结果为 4/7，不能写成真实模型 7/7 |
| Reranker 对照无可测增益 | `reports/retrieval-reranker-20260915.json` | 11 条正例过于简单，不支持提升结论 |
| Python 回归 382 项通过、3 项跳过 | README 中的隔离命令；测试目录 | 测试数证明工程回归，不等同业务效果 |

任何简历数字调整前先重跑对应脚本并保存原始报告，禁止把 mock、规则基线或单元测试通过率改写成真实模型准确率。

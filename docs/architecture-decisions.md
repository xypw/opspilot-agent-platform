# 架构取舍与验证边界

## 1. 为什么保留 Python 与 Java 两个服务

本项目模拟 Agent 接入独立订单业务服务，不代表公司遗留系统或线上客户项目。Python/FastAPI 负责检索、模型请求、工具编排和对话进度；Spring Boot 统一验证身份、订单归属、资格、金额及最终事务写入。页面直达 API 和 Agent 调用同一套业务规则。

只使用 FastAPI 也能实现鉴权、事务和幂等。选择双服务的价值是验证跨服务信任和一致性边界，代价是多一套接口、部署、身份传递及网络失败处理；不以“Java 天生更安全”或“Python 不能做事务”解释选型。固定的订单查询无需 Agent，多来源自然语言咨询才使用模型组合工具。

## 2. Redis 的两项明确职责

### 临时执行状态

RedisSaver 保存 LangGraph 的状态快照与执行进度，不保存图代码作为业务规则，也不是订单事实来源。新检查点默认 TTL 为 1440 分钟，读取不续期；这个 TTL 与 Java 草稿有效期不同，不能延长业务确认。配置范围为 1—10080 分钟。此变更不会自动给旧 Redis key 补 TTL。

检查点不存在、过期或归属不匹配时不恢复旧确认；运行中 Redis 故障返回 503，提醒先查原订单及草稿，不能承诺业务未执行。Redis 数据全部丢失时，待确认流程可能无法恢复。业务申请及幂等结果仍以 PostgreSQL 为准。PostgreSQL Checkpointer 是有效的简化替代方案，恢复能力并非 Redis 独占。

### 共享模型请求准入

`model_rate_limit.py` 在 `preview_tool_call.request_message()` 发 HTTP 之前调用 Redis Lua，原子完成计数检查、递增与过期。普通回答、证据审查及每一次重试共用入口；模拟模型不占配额。多个 API 实例须连接同一 Redis、使用同一账户别名与限额配置。

部署默认每 60 秒窗口允许 20 次请求。这只是演示预算，不是供应商官方限额。窗口从首个请求开启，窗口边界仍可能突发；它不限制同时进行的请求数，不统计 token，不影响其他应用直接使用同一供应商账户。因而不能保证消除供应商 429。

| 配置 | 含义 |
| --- | --- |
| `MODEL_RATE_LIMIT_REQUESTS` | 窗口请求数；0 关闭，Compose 默认 20 |
| `MODEL_RATE_LIMIT_WINDOW_SECONDS` | 窗口秒数，默认 60 |
| `MODEL_RATE_LIMIT_SCOPE` | 服务端账户别名，不是密钥或用户输入 |
| `REDIS_URL` | 所有实例连接的同一 Redis |

这些配置由进程环境读取，Compose 从项目 `.env` 注入。直接运行 Python 时须设置对应环境变量。没有配置限流时，离线代码默认关闭。

达到限额时不发送本次 HTTP 请求，返回本地 429 和 `Retry-After`；准入 Redis 故障时返回 503，不回退内存计数。证据工作流可能将准入异常转换为“审查失败/无足够证据”，但仍不会发出被拒绝的请求。之前的业务步骤可能已经执行，客户端不得因此重建申请。准入失败不计入模型 HTTP 尝试数。

Lua 超时可能已经占用名额，因此禁用 Redis 客户端透明重试，也不退还配额。Redis 重启丢失计数会重置预算；此方案用于流量保护而非财务扣费或安全权限。若要严格计费，必须使用持久化账本。所有实例更改限额/窗口应同步生效，否则不能声称统一预算。

## 3. PDF 解释与 Java 资格不能互相替代

针对带订单号的退货资格与政策联合咨询，完成条件要求订单查询、Java 资格查询和政策检索三个结果；缺任一项不能用模型结论替代。程序校验订单编号一致，明确展示 Java 资格决定。资料不能覆盖业务拒绝或替代用户确认。

这不是通用的自然语言政策矛盾检测器。当前回答提示资料适用性需核实，冲突应人工处理；不能宣称已识别所有 PDF 与业务规则不一致的问题。资料版本、正式审核发布流程仍是后续能力。

## 4. 评测数字的口径

- 历史 20 条真实模型评测衡量证据充分性判定及逐字引用校验，不是最终回答正确率。15 条有效且与预设标签一致，1 条语义误拒，4 条运行失败；运行失败不应剔除后宣传更高成功率。标签由项目编制，不是独立第三方人工盲评。
- 模拟模型的 8 条任务验证固定流程及约束，不是 LLM 的工具选择能力。
- 34 项 Java 测试中的 PostgreSQL 集成验证了并发重复确认及服务对象重建。对象重建不等于完整服务进程崩溃恢复。
- TracePilot 的确定性规划基线与真实模型调查路径分别报告，不能以规则基线的 12/12 代替模型修复率。

## 5. 2026-09-24 本次小范围验证

首次复测时本机 6380 不可达；现有 Redis 恢复运行后完成验证：62 项准入、检查点、业务边界与 API 检查全部通过，含 4 项真实 Redis 集成测试；另有 45 项证据审查与评测器相关检查通过。限定为本次相关测试，不是全量回归。

真实 Redis 验证包含两个独立客户端并发、两个独立 Python 进程共享配额、窗口实际过期后恢复、拒绝不续期及无 TTL 计数的保守处理。配额为 3 时，两进程共发起 8 次准入请求，恰好 3 次放行。测试只操作 UUID 命名的合成 key，结束时清理自身 key，无模型 HTTP 调用。测试入口为 `tests/test_model_rate_limit.py`，复现方式与结果见 [验证记录](../reports/runtime-boundaries-20260924.md)。

8 条固定离线模拟 Agent 任务再次全部通过，报告为 `reports/agent-v2-offline-20260924.json`。本次未启动或重建 Docker，也未调用外部模型；源码及 Compose 新配置尚未部署到运行实例。

TracePilot 14 项执行器/修复路径相关测试通过，包含凭据环境过滤及拒绝测试目标中的 Shell 语法。子进程环境白名单与临时目录不是系统级安全沙箱，不支持安全运行恶意仓库。

## 参考案例与官方资料

- [Anthropic：Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)：从简单工作流开始，区分模型决策与确定性流程。
- [LangGraph 客服案例](https://github.com/langchain-ai/langgraph/blob/main/examples/customer-support/customer-support.ipynb)：知识查询、业务工具与人工介入的组合场景。
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)：检查点机制与持久化选项。
- [LangGraph Redis 实现](https://github.com/redis-developer/langgraph-redis)：RedisSaver TTL 以分钟计，可控制读取续期。
- [Redis INCR](https://redis.io/docs/latest/commands/incr/) 与 [限流设计](https://redis.io/docs/latest/develop/use-cases/rate-limiter/)：共享计数、Lua 原子性、窗口算法的限制。

参考用于校准职责与实现，不代表本项目拥有这些项目的完整功能或生产保障。

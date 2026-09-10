# OpsPilot 学习与项目进度

> 最后更新：2026-09-09。这个文件记录会变化的学习状态；稳定教学规则在 `AGENTS.md`。

## 最终方向

- 目标岗位：Agent 开发实习、大模型应用开发实习，不以算法研究岗为目标。
- 投递时间：2026-09-26 开始投递。
- 交付要求：两个能够独立讲解的项目；先把 OpsPilot 做成可测试、可演示、可部署、可写进简历的主项目，再确定第二个互补项目的最终范围。

## 当前阶段

- 主项目：OpsPilot 企业知识库与售后工单 Agent。
- 当前学习主题：Agent 评测，不只判断回答“像不像对”，还要检查最终状态、工具调用顺序、必要事实、引用和运行异常。
- 当前核心练习：固定 JSON 用例已经接入可替换的批量 Agent 运行器，离线 Mock 基线已完成；下一步扩大测试集并做受控的真实模型评测。

## 已形成的项目能力

- FastAPI 接口和 Pydantic 请求/响应模型。
- 手写 Tool Calling 循环，以及工具参数校验和工具结果消息。
- 文档解析、切块、向量检索、关键词检索、混合检索、Reranker、引用与检索评测。
- LangGraph 状态、路由、多步工具执行、人工确认、中断和恢复。
- Redis Checkpoint、失败重试，以及高风险操作的确认状态。
- Java Spring Boot 业务服务、PostgreSQL 持久化、事务、幂等和业务冲突处理。
- Docker 化的本地依赖环境和分层测试。

这些条目表示项目中已经学习或实现过相应能力，不代表已经达到最终简历验收标准；后续仍需完整评测、演示、部署、README和面试复盘。

## 当前检查点

教师脚手架已经完成：

- `AgentEvaluationCase` 定义标准答案，包括 `expected_status`、`expected_tools`、必要文本和引用要求。
- `evaluation_data/agent_task_cases.json` 包含 5 条场景：单工具查询、RAG、订单加政策的多步工具、退货确认、优先级修改确认。
- `tests/test_agent_evaluation_data.py` 检查评测集结构和唯一 ID。
- `tests/test_agent_evaluation.py` 用 3 个测试定义当前学生练习的正确行为。

已经完成的核心判断：组合下面四个条件并返回一个布尔值，不新增循环框架或模型调用。

1. 实际状态等于预期状态。
2. 实际工具列表与预期工具列表顺序完全相同。
3. 每段必要文本都出现在回答中。
4. 要求引用时，回答中必须包含“来源：”。

### 2026-09-08 验证快照

- `python -m unittest tests.test_agent_evaluation_data`：1 个测试通过，评测数据结构有效。
- `python -m unittest tests.test_agent_evaluation`：3 个测试均在 `agent_evaluation.py` 的 `raise NotImplementedError` 处报错。
- 这 3 个错误是学生核心练习尚未实现造成的预期红灯，不是已经完成代码的回归故障。

### 2026-09-09 验证快照

- 已实现 `is_task_successful()`，使用四个可单独阅读的布尔条件判断状态、工具轨迹、必要文本和引用。
- `python -m unittest tests.test_agent_evaluation`：3 个测试通过。
- `python -m unittest tests.test_agent_evaluation_data`：1 个测试通过。
- 离线 Python 完整回归：279 个测试通过，3 个显式外部依赖测试跳过，0 个失败。

## 下一步验收

1. [x] 完成 `is_task_successful()`。
2. [x] 运行 `tests/test_agent_evaluation.py`，三个测试全部通过。
3. [ ] 学生能够解释为什么“答案看起来正确，但工具顺序错误”仍然是任务失败。
4. [x] 把固定评测数据接入批量执行器，统计任务成功率和失败原因，而不是只返回一个总分。

### 当前学生任务：失败原因收集

- 使用四个互相独立的 `if`，不能使用 `elif`，也不能在第一个错误后提前 `return`。
- 分别收集状态错误、工具轨迹错误、必要文本缺失和引用缺失。
- 一条运行有四种错误时，返回的列表必须同时包含四个原因。
- 2026-09-09 红灯基线：`tests.test_agent_evaluation` 共 5 个测试，4 个通过；`test_all_failure_reasons_are_collected` 因实际返回 `[]` 而失败，等待学生实现四个判断。
- 用户选择直接进入下一课后，教师补全四个判断；相关测试 6/6 通过。

### 当前课程：单条结果到批量指标

- 已新增 `AgentEvaluationResult`，统一保存 `case_id`、`success` 和 `failure_reasons`。
- 已新增 `evaluate_response()`，把标准用例和实际响应转换为结构化评测结果。
- 下一步指标包括用例总数、成功数、失败数、任务成功率，以及每种失败原因出现的次数。
- 一条失败用例可以贡献多个失败原因，因此失败原因次数之和可以大于失败用例数。
- 已实现 `AgentEvaluationSummary` 和 `build_evaluation_summary()`，可以输出总数、成功数、失败数、成功率、失败原因分布和逐条明细。

### 2026-09-09 单条结构化评测验证

- Agent 评测聚焦测试与数据测试：7 个测试全部通过。
- 离线 Python 完整回归：282 个测试通过，3 个显式外部依赖测试跳过，0 个失败。

### 2026-09-09 批量汇总验证

- 用户正确区分了“失败任务数量”和“失败原因出现次数”的统计口径。
- Agent 评测聚焦测试与数据测试：9 个测试全部通过。
- 离线 Python 完整回归：284 个测试通过，3 个显式外部依赖测试跳过，0 个失败。
- 下一步尚未执行真实模型；当前结果只证明评测器逻辑正确，不代表 Agent 的真实任务成功率。

### 2026-09-10 Agent 批量执行里程碑

- 新增 `agent_evaluation_runner.py`：加载并校验 JSON、拒绝重复 `case_id`、逐条调用可替换
  `AgentRunner`、隔离单条异常并生成批量汇总。
- `AgentRunner` 只接收 `case_id + question`，标准工具轨迹和答案要求不会传给被测 Agent；
  每条 LangGraph 用例生成独立 `thread_id`，避免 Checkpoint 和记忆串用例。
- 新增 `agent_evaluation_runtime.py`：`isolated` 环境使用冻结日期和本地虚构数据，完全不依赖
  Redis、PostgreSQL、Java 或外部模型；`app` 环境保留完整基础设施集成入口。
- `ConfiguredToolExecutor` 和 `build_agent_graph(..., tool_runner=...)` 支持按图注入工具依赖，
  修复了离线评测修改全局 `ORDER_QUERY` 后污染其他测试的问题。
- 离线命令运行 5 条标准用例：5 条成功、0 条失败、任务成功率 1.0；这只代表固定 Mock
  基线通过，不代表真实模型质量。
- 聚焦评测与工具测试：25 个测试通过。完整 Python 离线回归：290 个测试通过、3 个外部依赖
  测试跳过、0 个失败。
- `app` 环境首次运行在导入 `main` 时失败，最底层异常是 Redis
  `Authentication required`；已确认 `.env` 的 `REDIS_URL` 未携带密码，而当前 6379 服务要求
  认证。该基础设施配置问题尚未修复，不影响 `isolated` 评测基线。
- 学生待解释：为什么评测运行器不能把 `expected_tools` 传给 Agent，以及为什么每条用例必须
  使用独立 `thread_id`。

## 进度更新规则

- 每完成一个可验证里程碑，记录：完成内容、学生是否能解释、测试命令和结果、下一步。
- 遇到失败时记录最终异常、排错假设和验证结论，不只记录“AI 已修复”。
- 百分比必须对应明确清单；没有清单时只报告“已完成/进行中/未开始”。
- 不在这里记录 API Key、邮箱、真实客户数据或其他隐私信息。

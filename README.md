# OpsPilot

电商售后、企业知识库与工单协同 Agent，按可运行的小功能逐步开发。

项目级编码 Agent 约定见 [AGENTS.md](AGENTS.md)，真实的需求拆解、上下文选择、
测试与错误复盘示例见 [AI_CODING_WORKFLOW.md](AI_CODING_WORKFLOW.md)。项目内的
[回归验证Skill](.codex/skills/opspilot-regression/SKILL.md)按Python、Java和跨服务边界选择检查。

## 新增学习入口：Java 业务服务（2026-09-07）

第一步新增了 `business-service/` 只读订单接口和 Python HTTP 客户端，详见
[本节说明与练习](business-service/README.md)。使用虚构订单，不调用模型。
原 `/chat`、`/orders` 和默认订单工具尚未切换；下面较早的课程记录是阶段历史，
不能用来判断全部功能的当前完成度。

## 当前状态

- 已实现：FastAPI 健康检查、自动接口文档、工单查询、`POST /chat`（显式 mock/live 模式）、函数与接口测试、单次模型工具选择预览。
- 已实现脚本：在工单和订单两个只读工具中选择一个，校验参数、执行查询、回传结果、生成回答；最多两次模型请求，已通过本地模拟 HTTP 测试。
- 已提供独立的多轮 Agent 循环参考实现与离线演示，尚未接入 `/chat`，也未完成真实模型的多轮验收。
- 已实现：RAG混合检索与重排序、LangGraph状态与人工确认、Redis Checkpoint、
  PostgreSQL/pgvector、Java订单规则服务，以及退货草稿、预审、确认、幂等和冲突恢复流程。
- 已实现：Java 退货草稿与正式申请的可切换内存/PostgreSQL 存储，使用 Flyway V2 迁移、
  数据库事务、行锁和唯一约束保证跨重启恢复与 `draft_id` 幂等。
- 已实现：LangGraph 受限多步工具循环，单次任务最多执行 3 个工具步骤；响应返回
  `tool_trace`，RAG 证据跨步骤累计并由程序生成引用。
- 尚未完成：退货过期数据清理、统一可观测性、完整部署和真实模型任务评测。
- 已完成本地练习：解析 JSON 参数，用 Pydantic 校验并规范化编号，再调用查询函数。
- 当前真实闭环验证：接口曾返回 429 / 1305（模型访问量过大），仍需成功的真实请求验收。

## 第 3 天：从一个工具扩展到两个工具（进行中）

目标：同一个 Agent 根据问题选择 `query_ticket(ticket_id)` 或 `query_order(order_id)`，再由程序校验参数并调用对应函数。这是一个 Agent 使用两个工具，不是两个 Agent。

本节已准备：`orders.py` 中的虚构订单数据、`QueryOrderArgs` 参数模型、`QUERY_ORDER_TOOL` 工具说明和验收测试。
学习者已给出 `query_order()` 的循环查询逻辑，老师补齐冒号和缩进：按传入编号返回整条订单，找不到返回 `None`，不写死编号或修改数据。
`tests/test_orders.py` 的 3 项测试覆盖首条、末条和不存在的编号，并检查查询不修改数据。
已完成本地工具分发 `tool_executor.py`：按名称选择 `QueryTicketArgs` / `QueryOrderArgs`，校验后执行对应查询，未知工具抛出 `ValueError`。学习者给出订单分支后，老师修正了误用的工单参数模型。
可运行 `.\.venv\Scripts\python.exe -X utf8 tool_executor.py` 查看两种查询的本地结果，不读取密钥、不请求模型。`tests/test_tool_executor.py` 检查正常查询、参数字段传错、非法参数和未知工具，错误发生时两种查询都不执行。
已接入 `POST /chat`：`tool_schema.py` 的 `TOOLS` 提供两份工具说明，`extract_tool_preview()` 检查允许的工具名，`agent_roundtrip.py` 调用 `execute_tool(name, arguments)` 分发。仍然是每次执行一个工具、最多两次模型请求，不是多步循环。

本地自动化验收已覆盖：工单/订单正确分发、必填参数字段传错时不执行查询、未知工具拒绝、查无记录回传 `null`、多个工具调用拒绝、请求之间不混淆状态。额外字段仍按当前参数模型的默认规则忽略，严格拒绝额外字段留待后续完善。
模拟模式只能验证路由代码，不代表真实模型选工具的准确率；live 路径也用离线 HTTP 替身做了订单集成测试，真实选择和回答质量仍需单独验收。

手动验收：在 `/docs` 的 `POST /chat` 使用 `mode: "mock"`，分别查询 `T-1002`、`O-2003` 和 `O-9999`，观察 `tool_name`、`tool_result` 与 `answer`；再在一个问题里同时写两个不同编号，检查当前单次查询边界。
下一步关注业务场景和执行轨迹，不重复默写已理解的类名或语法。学习者负责核心函数和场景验收，老师负责连接代码、测试与 Code Review。

### 有次数上限的 Agent 循环（参考答案，独立演示）

学习者表示暂时不会组合循环，老师应请求在 `agent_loop.py` 提供了 `run_agent()` 的完整参考实现；这不代表学习者已经能独立完成。
每轮最多接受一个允许的工具调用，执行并将原始 assistant 消息、对应 tool 结果依次加入历史；模型不再调用工具且返回非空文字时，函数返回该文字。
模型请求总数由 `max_steps` 限制（默认 5），最终回答也占一次请求。达到上限仍未返回文字时抛出 `RuntimeError`，不再追加请求；已经完成的只读查询不会撤销。校验或网络失败也直接停止，不自动重试。
返回询问编号也会结束本次运行，不能把所有文字返回都计为业务任务成功。当前不支持每轮多个工具并行执行、跨请求记忆、恢复或写操作。

运行 `.\.venv\Scripts\python.exe -X utf8 demo_agent_loop.py`：预设的模拟模型先请求订单 O-2001，再请求工单 T-1003，第三轮根据实际查询结果汇总。真实模型请求为 0，模拟模型请求为 3；不读取 `.env`。预设响应不能证明真实模型的规划能力。
`tests/test_agent_loop.py` 覆盖历史消息顺序、调用 ID 对应、提前返回、非法参数、未知工具、请求次数上限、查询不存在、失败不重试和独立运行不串状态。
`/chat` 继续使用已验证的单次查询流程；多轮接口接入及响应轨迹结构留待下一步。循环的 system 提示单独允许分轮查询，不改变旧接口的单次查询约束。

### 高风险工具与人工确认

模型可见的写操作工具名为 `request_priority_change`，只创建 `pending` 操作并返回随机 `action_id`；内部真正修改数据的 `change_ticket_priority` 不在模型工具列表中。提示词也要求模型告知用户操作仍待确认，不能声称已经修改。

`POST /chat` 的 mock 模式已接入这条路径。输入“把工单 T-1003 的优先级改成 high”，规则模拟器会生成 `request_priority_change` 工具调用，真实执行参数校验和待确认操作创建，并在 `tool_result` 返回 `action_id`；此时真实模型请求数仍为 0。查询措辞仍选择 `query_ticket`。mock 只按有限的修改关键词、编号和优先级模拟选择，不代表真实模型理解能力。

确认边界分为两个 HTTP 请求：

1. `POST /actions/priority-changes`，请求体例如 `{"ticket_id":"T-1003","new_priority":"high"}`。返回 `status: "pending"`，此时工单保持不变。
2. `POST /actions/{action_id}/confirm`，根据第一步的 ID 确认。执行后返回 `status: "executed"`，工单优先级才改变。

相同 `action_id` 重复确认会返回已执行结果，不再次调用修改函数；未知操作或工单返回 404，非法优先级返回 422。当前优先级只允许 `low`、`medium`、`high`，参数模型拒绝额外字段。
内存仓库用锁保护同进程内的“检查状态并执行”步骤，但这不是可部署的持久化方案：服务重启会丢失待确认操作，多进程之间也不共享状态。后续将迁移到数据库事务与持久化 checkpoint。

运行 `.\.venv\Scripts\python.exe -X utf8 demo_confirmation.py` 可观察申请前后状态；演示会恢复虚构工单数据，不读取密钥、不调用模型。
服务仍只监听本机且没有用户鉴权，人工确认接口不得直接暴露到公网。后续需要把确认操作绑定到发起用户/会话，并记录审计信息。

### Agent Checkpoint：Interrupt / Resume

`POST /agent/runs` 启动一次只使用 mock 的可恢复运行，请求体包含 `thread_id`、`idempotency_key` 和 `message`。修改申请返回 `run_id`、`status: "WAITING_CONFIRMATION"`、`pending_action_id` 和 `version: 1`；查询任务会直接返回 `COMPLETED`。

相同 `thread_id + idempotency_key + message` 的网络重试直接返回同一个 Checkpoint，不重新调用 Agent 或创建操作。同一会话中把相同幂等键用于不同消息会返回 409，避免覆盖原运行。不同会话可独立使用相同幂等键。

`GET /agent/runs/{run_id}` 读取 Checkpoint；`POST /agent/runs/{run_id}/confirm` 确认并恢复暂停运行。成功后状态变成 `COMPLETED`、版本变成 2；重复确认返回相同结果。没有待确认操作的已完成查询返回 409，未知运行返回 404。

当前恢复阶段由确定性的本地状态机生成 `[模拟恢复]` 回答，没有再次请求模型；它证明状态转换和幂等边界，不证明真实模型的恢复推理能力。Checkpoint 仍位于单进程内存中，也没有保存完整模型消息历史，重启即丢失。后续持久化到数据库并保存完整消息/工具轨迹后，才能实现跨重启恢复。

本节查询函数在 `tickets.py` 的 `query_ticket()`。模拟数据不包含真实业务信息。
`GET /tickets/T-1003` 返回整条模拟工单（200），`GET /tickets/T-9999` 返回工单不存在（404）。

这是主项目的初始脚手架，不是已完成的 Agent。独立于 Agent Reliability Lab，也不整合旧教学网站。

## 本地运行（Windows PowerShell）

在本项目目录中执行；首次使用需要 Python 3.11 或以上：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8010
```

- 健康检查：http://127.0.0.1:8010/health
- 接口文档：http://127.0.0.1:8010/docs
- 文档页面依赖外部 Swagger UI 静态资源；如果页面空白，可先访问健康检查和 `/openapi.json`。
- 服务只监听本机，不是线上部署。
- 当前没有鉴权和多用户限流，只允许本机教学；不要直接绑定公网或将真实模式公开部署。

## 第 2 天收尾：POST /chat

在接口文档中展开 `POST /chat`，点击 Try it out，发送：

```json
{"message": "帮我查工单 T-1003", "mode": "mock"}
```

默认 `mock` 不读取密钥，不出网、不消耗模型额度。它只用规则从问题中提取唯一的 `T-四位数字` 或 `O-四位数字` 编号，按前缀模拟选择工单或订单工具，并模拟两次模型响应；参数校验、对应查询、工具消息拼装仍运行项目代码。它不理解完整语义，也不验证问题中的业务名词是否与编号一致，不是真实模型的意图识别或回答能力。
响应明确包含 `mode: "mock"`、`is_mock: true`、`model: "mock"`、`model_requests: 0`、`simulated_model_requests: 2`，回答也带 `[模拟模式]`。

显式改为 `"mode": "live"` 才会读取本地 `.env` 并调用固定的免费模型。服务收到的 `message` 会传入两次模型请求，不再固定查 T-1003。真实模式失败时返回错误，不会静默切换到模拟模式。
每次请求独立，暂无跨请求会话记忆；当前只支持一次工单或订单查询，不支持通用聊天或多步任务。真实模型第一次未返回工具调用（例如询问缺失编号）时，当前流程仍返回 502，不把它当作已完成查询；澄清对话留待后续实现。

关键文件：`chat_models.py` 定义请求/响应；`main.py` 注册路由并映射错误；`chat_service.py` 选择真实或模拟流程。
`ChatRequest` 相当于请求 DTO。FastAPI 从请求体 JSON 创建并校验模型，进入 `post_chat()` 后可直接使用 `request.message`，不用再次 `json.loads()`。

状态约定：

- 200：聊天请求完成；查不到工单或订单时仍可完成聊天，`tool_result` 为 `null`。
- 400：模拟模式无法提取唯一、格式正确的工单或订单编号。
- 422：请求体不符合要求，例如空白 message、错误类型、超过 1000 字符、未知字段或非法 mode。
- 502：模型网络请求失败、响应无效或生成的工具参数非法。
- 503：真实模式密钥配置不可用、上游限流或凭据/权限不可用。
- 504：模型请求超时。

这些响应不包含请求头、原始模型错误正文或密钥。真实模式仍需后续成功的端到端服务验收，本次验证仅执行 mock 和离线测试。

本节练习：在接口文档中分别试查询 T-1002 和提交纯空白 message，记录响应并说明请求校验发生在进入业务函数之前还是之后。
阶段复习：请求体 DTO、Pydantic 自动校验、路由与业务函数分工、模拟结果标识、上游错误映射。
面试自检：为什么请求体错误返回 422，而模型服务不可用返回 503？为什么不能把 live 失败自动伪装成 mock 成功？

## 运行测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

`test_api.py` 同时包含替代查询结果的接口层测试，以及连接真实查询函数的集成测试。
`test_tickets.py` 的 3 项测试验收真实查询逻辑：首条、末条与不存在的编号。

验收要求：能查到第一条与最后一条工单，查不到时返回 `None`；不写死编号，不修改模拟数据。

## Tool Calling：先观察模型选择，不执行工具

`tool_schema.py` 是发送给模型的工具说明，包含函数名、用途和参数规范；不包含本地工单数据。
`preview_tool_call.py` 只发送一次模型请求，展示模型返回的函数名与参数字符串，不执行查询，也不生成最终工单回答。

```powershell
.\.venv\Scripts\python.exe -X utf8 preview_tool_call.py
```

此命令会读取本地密钥并请求外部服务；普通单元测试使用模拟响应，不需要密钥或联网。
2026-08-31 的一次真实请求已成功选择 `query_ticket` 并返回参数字符串 `{"ticket_id":"T-1003"}`。
同次调试较早的请求曾返回 HTTP 429；仅凭状态码无法确定具体原因，不声称已经排除账户或限流问题。
脚本固定官方地址和 `glm-4.7-flash`，不跟随重定向、不自动重试、不回退到其他模型。

学习者已在 `execute_tool_example.py` 中完成 `json.loads()` 解析和查询函数调用，可独立运行：

```powershell
.\.venv\Scripts\python.exe -X utf8 execute_tool_example.py
```

此脚本单独运行时使用与先前模型返回相同的固定示例参数，不读取密钥、不请求模型；现在保留为第 2 天练习，`agent_roundtrip.py` 已改用 `tool_executor.py` 连接模型输出。
本地流程已接入 `QueryTicketArgs.model_validate()`：缺失编号、非字符串编号或纯空白编号会抛出 `ValidationError`，查询不会执行；带两侧空白的有效编号会规范化后再查询。它仍不是完成的通用执行器，额外字段限制和统一错误结果尚待完善。
`agent_roundtrip.py` 已添加回传连接，当前是两个工具可选、每次只执行一个的有边界教学流程，不是完成的通用 Agent。

`tool_args.py` 中的 `QueryTicketArgs` 已接入 `execute_query_example()` 并通过测试：编号必填且为字符串，先去除两侧空白，再检查最小长度为 1。`model_config` 保存规则，`model_validate()` 执行校验；返回的是模型对象，使用 `.ticket_id` 读取字段。

## 工具结果消息

`tool_messages.py` 中的 `build_tool_message()` 已由学习者在提示下完成。返回字典包含三个字段：

- `role`：固定为 `tool`。
- `tool_call_id`：模型生成的那次工具调用 ID，不是工单编号；本地示例使用明确标注的模拟 ID。
- `content`：`json.dumps(result, ensure_ascii=False)` 生成的 JSON 字符串。当前查不到工单时 `None` 序列化为 `null`；更明确的错误结果格式留待后续完善。

```powershell
.\.venv\Scripts\python.exe -X utf8 tool_messages.py
```

此命令演示本地查询及工具消息构造，不发送模型请求。`test_tool_messages.py` 的 4 项验收测试已通过。

## 运行查询闭环（两个工具可选，每次执行一个）

```powershell
.\.venv\Scripts\python.exe -X utf8 agent_roundtrip.py
```

此脚本最多向智谱官方接口发送两次真实请求：

1. 把固定模拟问题和工具说明发给模型。
2. 检查只返回一个允许的查询工具，并检查调用 ID；解析、校验参数后执行本地查询。
3. 通过 `tool_executor.py` 的分支选择参数模型与查询函数；保留原问题及 assistant 工具调用消息，追加对应的 tool 结果消息。
4. 第二次请求不再提供工具，让模型基于结果回答；若仍返回工具调用或空回答，停止并报告未完成。

不自动重试，不执行未知工具，不写入或修改工单。参数错误会在查询前阻止执行。普通测试使用模拟 HTTP 响应；它们验证控制流程，不代表真实模型的回答准确率。
真实模型只接收问题、工具说明和本次查询的模拟工单或订单结果；不是整个项目或密钥文件。不要在真实模式的用户问题中提交私人资料。

阶段验收与复习：能说明工具名与参数是谁生成的、Python 由谁执行、`model_validate()` 在何时校验，以及 `tool_call_id` 为何必须匹配。
相关面试题：为什么这个流程需要两次模型请求？如何阻止非法参数或未知工具执行？第二次模型请求失败时，查询是否已经发生？

## 密钥与费用

本地 `.env` 用于保存自己的 `ZHIPU_API_KEY`，已加入 `.gitignore`。其他人克隆项目后可复制 `.env.example` 为 `.env`，仅在本地填写密钥。

不要把真实密钥放进聊天、截图、源码、测试数据或提交历史。`.gitignore` 防止误暂存，但不是加密；不要强制添加 `.env`。

健康检查、GET 工单查询和 mock 聊天不读取密钥、不调用模型。显式运行 `preview_tool_call.py`、`agent_roundtrip.py`，或请求 `POST /chat` 并选择 `live`，才会读取 `.env` 并调用智谱官方列为免费的 `glm-4.7-flash`，不自动切换付费模型（特别是 `glm-4.7-flashx`）。真实调用前需核对账户配额与官方价格，不承诺永久或无限免费。聊天请求体不接受 API Key。

## 本节只理解这一个映射

访问 `GET /health` → 执行 `health()` → 把返回的 Python 字典转换为 JSON 响应。

类比 Java：`@app.get("/health")` 类似 `@GetMapping("/health")`。

参考：[FastAPI 第一步](https://fastapi.tiangolo.com/tutorial/first-steps/)、[接口测试](https://fastapi.tiangolo.com/tutorial/testing/)。

后端参考：[请求体](https://fastapi.tiangolo.com/tutorial/body/)、[错误处理](https://fastapi.tiangolo.com/tutorial/handling-errors/)、[同步与异步路由](https://fastapi.tiangolo.com/async/)。

模型参考：[智谱工具调用](https://docs.bigmodel.cn/cn/guide/capabilities/function-calling)、[价格](https://bigmodel.cn/pricing)、[业务错误码](https://docs.bigmodel.cn/cn/api/api-code)。

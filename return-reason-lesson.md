# 退货原因补全：LangGraph 暂停与恢复

本节使用 `/agent-graph/runs`；旧 `/chat` 不提供多轮原因补全。
模型选择工具，Java 提供资格结果，LangGraph 保存订单和原因。

当 Java 返回 REASON_REQUIRED，且 return_reason 为 null 时，read_tool 保存
查询结果，条件边先选择 prepare_return_draft，在 Java 创建限时草稿。
草稿有效时进入 ask_return_reason，该节点调用 interrupt。
恢复时 ask_return_reason 从头执行，interrupt 返回用户提交的数据。
Java 查询在前一节点完成，因此补充原因不会重复查询。

启动请求示例（需要 Java 服务可用；mock 只模拟模型）：

```json
{"thread_id":"return-demo-1","message":"订单 O-2001 能退货吗","mode":"mock"}
```

仅当返回 WAITING_REASON 时，通过
`POST /agent-graph/runs/return-demo-1/return-reason` 提交：

```json
{"return_reason":"键盘按键失灵"}
```

也可以在启动请求中提供 return_reason，避免重复询问。本节暂未从自然语言中自动提取原因。
订单日期按 Java 的业务时钟计算；演示订单不会永远处于第 8～15 天，自动测试使用固定替身结果。

补充原因后，finish_reason_collection 调用
`POST /api/orders/{orderId}/return-draft/{draftId}/reason`。
Java 检查草稿 expires_at，受理并保存预审结果。原订单号和草稿号从 Checkpoint 取出。
失败时可用相同原因及类别重试保存下来的节点；Java 对相同成功请求返回原结果。
此处尚未实现 LangGraph 并发恢复保护，Java 单进程仓库用同步锁保护提交。

草稿通过 POST /api/orders/{orderId}/return-draft 创建，started_at 和 expires_at
均由 Java 产生，有效期恰好3600秒；同一订单重复创建返回原草稿，不重置时间。
第15天23:30创建，第16天00:29:59仍可补充；00:30:00起失效，返回410 DRAFT_EXPIRED。
草稿保存初始资格，跨日可以继续，但订单取消或签收日期改变仍会阻止提交。
状态查询读取 Java 当前草稿状态，对外显示 EXPIRED；Checkpoint 无定时后台清理，
提交时仍由 Java 强制拒绝超时数据，不能只依赖页面显示。

原因类别 reason_code 支持 PERSONAL_PREFERENCE、QUALITY_ISSUE、OTHER（默认）。
自然语言自动分类尚未实现；类别是用户声明，不是核实后的事实。
演示商家预审规则：7天内 ACCEPTABLE；第8～15天个人喜好 REJECTED，
质量问题和其他原因 MANUAL_REVIEW。第16天不能新建草稿，但已创建且未满1小时的草稿仍可继续。
这是项目教学规则，不是对所有商家/商品的统一政策说明。

响应 return_review 保存 Java 的 decision 与 reason。MANUAL_REVIEW 表示需要人工核实，
尚未创建审核工单，也不会自动创建退货申请。只有 NO_REASON_ALLOWED 或预审 ACCEPTABLE
才能进入创建申请前的确认节点。
原因恢复接口只接收原因，不能改订单号。未知会话返回 404，非等待原因状态返回 409，
空白原因返回 422。thread_id 不能用启动接口覆盖。

## 创建退货申请前的人工确认

确认时 Java 还会比较订单商品和金额与草稿快照是否一致。变化时返回
`409 {"code":"ORDER_CHANGED"}`，Python 网关转换为独立的 ReturnOrderChanged
业务异常，FastAPI 统一返回409，detail包含code和中文message。未知409、
非JSON错误和服务故障仍按上游异常处理，不能仅凭错误文字猜测订单发生变化。
已有申请的幂等重试优先返回原申请，不受后续订单金额变化影响。

LangGraph 在确认冲突后保存 `STALE_CONFIRMATION` 状态，因此刷新页面不会继续显示
旧的 `WAITING_CONFIRMATION`。该状态不能再次调用普通 resume，且正式申请保持为空。
客户端可调用 `POST /agent-graph/runs/{threadId}/refresh-return-confirmation`。
Python 会重新查询服务器资格，Java 仅在订单快照确实变化时生成新的 draft_id；旧草稿
立即失效，旧 approved 值被清除。最新资格仍为七天无理由时进入 WAITING_CONFIRMATION；
若跨入第8～15天，则进入 WAITING_REASON 并重新收集原因；已经不符合资格时结束流程。
刷新没有变化的草稿返回冲突，不能用它反复延长一小时有效期。

七天无理由资格不要求填写原因，但也不能由模型直接创建申请。LangGraph 先调用
`POST /api/orders/{orderId}/return-draft`，然后在 `await_return_confirmation` 节点执行
`interrupt`。Checkpoint 保存订单号、商品、退款金额、草稿号和截止时间，API 返回
`WAITING_CONFIRMATION`。这些字段来自 Java 订单数据，不采用模型自己生成的金额。

客户端确认时调用 `POST /agent-graph/runs/{threadId}/resume`：

```json
{"approved":true}
```

`approved=true` 进入 `execute_return`，Python 调用 Java 的
`POST /api/orders/{orderId}/return-draft/{draftId}/confirm`。Java 再检查草稿是否过期、
订单是否改变以及资格或预审是否允许，然后创建状态为 `SUBMITTED` 的退货申请。
响应的 `return_application` 包含服务器生成的 application_id、商品、退款金额和创建时间。
此处只创建申请，不代表退款已经执行。

`approved=false` 进入 `cancel_return`，Java 把草稿标记为 `CANCELLED`，不创建申请。
同一草稿重复确认返回同一个申请，因此网络超时后的安全重试不会重复创建业务数据。
LangGraph 拒绝对已结束会话再次 resume；即使编排层发生重复调用，Java 幂等边界仍然生效。
确认恰好发生在一小时截止时间时返回 410，工作流显示 `EXPIRED`，且
`return_application` 保持为空。

Java 退货流程现在通过 `ReturnDraftStore` 解耦业务规则和存储。默认 `memory` 便于离线测试；
设置 `RETURN_DRAFT_STORE_BACKEND=postgres` 后，Flyway V2 创建 `return_drafts` 与
`return_applications`，服务重启后仍可读取原申请。
PostgreSQL 事务和行锁保护“读取状态、校验、写入”的完整过程；`order_id` 保证同一订单只有
一个当前草稿，`return_applications.draft_id` 唯一约束保证同一草稿最多创建一个申请。
过期记录暂保留用于识别重复请求，总量上限10000；自动归档和物理清理仍是上线前工作。

本节验证：`python -m unittest tests.test_return_reason_flow tests.test_agent_graph`
使用 FastAPI TestClient 和真实 LangGraph，Java 响应为替身；Java 的确认、取消和幂等规则
另由 Spring Boot HTTP 测试覆盖。这些测试不代表真实模型效果，模型工具选择仍需评测集验证。
真实 PostgreSQL 的跨服务实例幂等性由 `PostgresReturnDraftIntegrationTest` 覆盖；该测试默认
跳过，显式设置 `RUN_POSTGRES_INTEGRATION=true` 后才连接本地开发数据库。

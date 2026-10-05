# OpsPilot Java 业务服务

`business-service` 是 OpsPilot 的 Java 后端模块，负责订单查询、退货资格判断、退货原因预审，以及退货草稿和正式申请的管理。

它使用 **Java 17、Spring Boot 3.5.16 和 Maven**。默认地址为 `http://127.0.0.1:8081`，通过 HTTP 向 Python 服务提供业务能力。

## 1. 在整个项目中的职责

OpsPilot 面向电商售后与工单协作场景。Python 负责模型调用、RAG、工具调度和确认交互；Java 负责执行确定性的业务规则与业务写入。

```text
用户 → Python API / Agent → Java HTTP 接口
                              ├─ 查询订单及其归属
                              ├─ 判断退货资格、预审退货原因
                              └─ 保存草稿、确认创建退货申请
```

例如，用户询问“这笔订单能退吗”，Python 调用 Java 的资格接口，再解释返回的结论。签收天数和资格由 Java 计算，不能由模型自行修改。

当前 Java 模块包含：

- **订单查询**：校验订单号，并限制用户只能查询自己的订单。
- **资格判断**：按签收日期和订单状态返回三态资格。
- **原因预审**：返回可接受、拒绝或需要人工审核的结论。
- **草稿流程**：创建、查询、提交原因、取消、刷新快照及确认申请。
- **幂等确认**：对同一个 `draft_id` 重复确认，返回已有申请。
- **可替换存储**：默认使用内存；可配置 PostgreSQL 保存草稿和申请。
- **双重认证**：默认同时检查用户 Bearer Token 和内部服务令牌。

当前订单仍来自 `DemoOrderRepository` 的三条虚构内存数据。PostgreSQL 后端持久化的是退货流程数据，并没有把订单仓库切换成数据库。正式申请状态 `SUBMITTED` 表示申请已创建，**不代表已经退款**；`MANUAL_REVIEW` 也只是预审结论，当前模块没有人工审批完成接口。

## 2. 代码结构与阅读顺序

生产代码位于 `src/main/java/dev/opspilot/business/`。

| 文件 / 类 | 作用 |
| --- | --- |
| `BusinessApplication` | Spring Boot 启动入口，提供统一的上海时区业务时钟 |
| `security/ServiceAuthenticationFilter` | 用户令牌和内部服务令牌校验，绑定当前用户 |
| `security/HealthController` | 提供健康检查接口 |
| `orders/OrderController` | 订单查询、退货资格和独立原因预审的 HTTP 入口 |
| `orders/DemoOrderRepository` | 模拟订单数据和订单归属检查 |
| `orders/OrderResponse` | 订单响应 DTO；金额以整数分表示 |
| `orders/ReturnEligibilityService` | 按订单状态和签收日期计算资格 |
| `orders/ReturnReviewService` | 校验退货原因并输出预审结果 |
| `orders/ReturnDraftController` | 草稿流程的 HTTP 入口 |
| `orders/ReturnDraftService` | 草稿有效期、订单快照、确认与幂等规则 |
| `orders/ReturnDraftStore` | 草稿存储接口 |
| `orders/InMemoryReturnDraftStore` | 本地演示与离线测试使用的内存实现 |
| `orders/PostgresReturnDraftStore` | PostgreSQL 实现，使用事务、行锁和唯一约束 |
| `orders/PostgresReturnStoreConfiguration` | 创建数据库组件并执行 Flyway 迁移 |

建议先沿着 `OrderController → DemoOrderRepository → OrderResponse` 阅读查询链路，再看资格判断，最后看 `ReturnDraftService` 和两种存储实现。

配置文件是 `src/main/resources/application.properties`，数据库迁移文件位于 `src/main/resources/db/migration/`，测试位于 `src/test/java/`。

## 3. 在 IDEA 中打开并启动

### 打开模块

在 IDEA 中选择 **Open**，打开 `business-service` 目录或其中的 `pom.xml`，按 Maven 项目导入。项目 SDK 和运行配置 JRE 使用 **JDK 17**。

### 最小本地演示配置

找到 `dev.opspilot.business.BusinessApplication`，为它创建或编辑 Application 运行配置，在 **Environment variables** 中添加：

```text
OPSPILOT_AUTH_REQUIRED=false
RETURN_DRAFT_STORE_BACKEND=memory
```

这组配置用于本机模拟数据演示：关闭认证后使用 `local-demo-user`，可以查询三条演示订单；草稿与申请只保存在内存中，重启 Java 服务会清空。该配置不用于公开部署。

点击 `BusinessApplication.main()` 旁的运行按钮。服务监听 `127.0.0.1:8081`。此演示无需启动 Python、模型、Redis 或 PostgreSQL。首次 Maven 导入可能下载项目依赖；本地已有依赖时直接复用。

也可以在 `business-service` 目录的 PowerShell 中启动：

```powershell
$env:OPSPILOT_AUTH_REQUIRED = 'false'
$env:RETURN_DRAFT_STORE_BACKEND = 'memory'
mvn spring-boot:run
```

以下演示命令在另一个 PowerShell 窗口执行。

## 4. 基本演示

演示使用的订单如下。`O-2001` 的签收日期在服务启动时设置为业务当天的七天前；如果服务跨日运行，查询时的签收天数会相应增加。

| 订单号 | 商品 | 状态 | 金额（分） | 所属用户 |
| --- | --- | --- | --- | --- |
| `O-2001` | 机械键盘 | `delivered` | 39900 | `U-1001` |
| `O-2002` | 无线鼠标 | `processing` | 12900 | `U-1002` |
| `O-2003` | 显示器 | `cancelled` | 159900 | `U-1001` |

### 4.1 健康检查

```powershell
$baseUrl = 'http://127.0.0.1:8081'
Invoke-RestMethod "$baseUrl/health" | ConvertTo-Json
```

预期返回：

```json
{"status":"ok","service":"opspilot-business"}
```

`/health` 不要求认证；它只说明 HTTP 服务能响应，不代表完整业务流程已经验证。

### 4.2 查询订单

```powershell
Invoke-RestMethod "$baseUrl/api/orders/O-2001" | ConvertTo-Json
```

返回字段为 `id`、`status`、`product`、`delivered_at` 和 `amount_cents`。其中 `39900` 分表示 399 元，签收日期以本次服务启动时生成的日期为准。

再观察两类错误：

```powershell
# 编号格式有效，但没有这条订单：404 / ORDER_NOT_FOUND。
Invoke-RestMethod "$baseUrl/api/orders/O-9999"

# 编号不是 O- 加四位数字：400 / INVALID_ORDER_ID。
Invoke-RestMethod "$baseUrl/api/orders/T-1001"
```

PowerShell 对非成功 HTTP 状态会显示错误，这是预期的接口拒绝结果。

### 4.3 查询退货资格

```powershell
Invoke-RestMethod "$baseUrl/api/orders/O-2001/return-eligibility" | ConvertTo-Json
Invoke-RestMethod "$baseUrl/api/orders/O-2002/return-eligibility" | ConvertTo-Json
Invoke-RestMethod "$baseUrl/api/orders/O-2003/return-eligibility" | ConvertTo-Json
```

服务在启动当天查询 `O-2001` 时，预期返回：

```json
{
  "order_id": "O-2001",
  "decision": "NO_REASON_ALLOWED",
  "can_apply": true,
  "reason_required": false,
  "days_since_delivery": 7,
  "reason": "WITHIN_7_DAY_NO_REASON_WINDOW"
}
```

`O-2002` 返回 `NOT_ALLOWED / ORDER_NOT_DELIVERED`，`O-2003` 返回 `NOT_ALLOWED / ORDER_CANCELLED`。这些都是成功计算出的业务结论，HTTP 状态为 200。

资格规则按上海时区自然日计算：

| 签收后天数 / 状态 | `decision` | 含义 |
| --- | --- | --- |
| 0～7 天，已签收 | `NO_REASON_ALLOWED` | 可以无理由申请退货 |
| 8～15 天，已签收 | `REASON_REQUIRED` | 可以提出申请，但需要原因预审 |
| 超过 15 天、未签收或已取消 | `NOT_ALLOWED` | 当前不允许申请 |

原因预审接受 `PERSONAL_PREFERENCE`、`QUALITY_ISSUE`、`OTHER` 三类原因。8～15 天的个人喜好理由会被拒绝；质量问题或其他理由返回 `MANUAL_REVIEW`，不会直接获得确认申请的资格。三条默认订单没有 8～15 天样例，该边界可以阅读 `ReturnReviewTest` 和 `ReturnEligibilityServiceTest`。

### 4.4 创建草稿并查看确认内容

```powershell
$draft = Invoke-RestMethod -Method Post "$baseUrl/api/orders/O-2001/return-draft"
$draft | ConvertTo-Json
$draftId = $draft.draft_id
```

响应包含 `draft_id`、`order_id`、`product`、`amount_cents`、`started_at`、`expires_at` 和 `status`。草稿有效期为一小时；创建后尚未提交正式申请。

当前新草稿的状态字段为 `WAITING_REASON`。这个名称不能单独用于判断是否必须提供原因：对于 `NO_REASON_ALLOWED` 的订单，Java 允许直接确认。

先核对草稿中的订单、商品、金额和有效期。**只有你决定确认这份演示草稿后，再执行下一步。**

### 4.5 确认申请并演示幂等重试

```powershell
$application = Invoke-RestMethod -Method Post "$baseUrl/api/orders/O-2001/return-draft/$draftId/confirm"
$application | ConvertTo-Json
```

预期得到 `application_id`、`order_id`、`product`、`refund_amount_cents`、`status` 和 `created_at`，状态为 `SUBMITTED`。这是模拟退货申请，不会调用支付系统退款。

对同一草稿重复确认：

```powershell
$retry = Invoke-RestMethod -Method Post "$baseUrl/api/orders/O-2001/return-draft/$draftId/confirm"
$retry.application_id -eq $application.application_id
```

预期为 `True`：同一个草稿返回同一份申请，不会因为网络重试重复创建。

确认时，Java 还会检查草稿是否取消、是否过期，以及订单商品、金额、状态和签收日期是否与草稿快照一致。快照改变会返回 `409 / ORDER_CHANGED`；刷新草稿需要使用新 `draft_id`，上层应重新展示并取得用户确认。人工确认交互由 Python / 前端负责，Java 不通过模型输出推断用户是否同意。

## 5. HTTP 接口速查

除 `/health` 外，默认都需要认证。以下 `{orderId}` 和 `{draftId}` 替换为真实的演示响应值。

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/health` | 健康检查 |
| GET | `/api/orders/{orderId}` | 查询当前用户的订单 |
| GET | `/api/orders/{orderId}/return-eligibility` | 查询退货资格 |
| POST | `/api/orders/{orderId}/return-review` | 独立的只读原因预审，不创建申请 |
| POST | `/api/orders/{orderId}/return-draft` | 创建或返回已有草稿 |
| GET | `/api/orders/{orderId}/return-draft/{draftId}` | 查询草稿 |
| POST | `/api/orders/{orderId}/return-draft/{draftId}/reason` | 提交原因并保存草稿预审结果 |
| POST | `/api/orders/{orderId}/return-draft/{draftId}/confirm` | 确认并创建申请，重复请求返回原申请 |
| POST | `/api/orders/{orderId}/return-draft/{draftId}/cancel` | 取消尚未提交申请的草稿 |
| POST | `/api/orders/{orderId}/return-draft/{draftId}/refresh` | 订单快照改变后刷新草稿 |

两个原因接口的 JSON 请求格式相同：

```json
{"return_reason":"商品存在质量问题","reason_code":"QUALITY_ISSUE"}
```

独立 `/return-review` 不更新草稿。需要保存草稿预审结果时，应调用草稿的 `/reason` 接口。预审返回 `ACCEPTABLE`、`REJECTED` 或 `MANUAL_REVIEW`，业务拒绝不等同于 HTTP 服务故障。

## 6. 认证与存储配置

### 默认认证模式

省略 `OPSPILOT_AUTH_REQUIRED` 时默认启用认证。调用方必须携带：

```text
Authorization: Bearer <用户令牌>
X-OpsPilot-Service-Token: <内部服务令牌>
```

`OPSPILOT_AUTH_TOKENS` 是 JSON 格式的用户令牌映射，令牌至少 16 个字符。例如，以下仅为虚构的配置示意：

```json
{"demo-user-token-1001":{"user_id":"U-1001","roles":["customer"]}}
```

`OPSPILOT_INTERNAL_SERVICE_TOKEN` 配置内部服务令牌，也至少 16 个字符。用户 `U-1001` 能看到 `O-2001` 和 `O-2003`，查询属于 `U-1002` 的 `O-2002` 会返回 `404 / ORDER_NOT_FOUND`。

认证配置缺失返回 `503 / AUTH_NOT_CONFIGURED`；用户令牌无效返回 401；内部服务令牌无效返回 `403 / TRUSTED_SERVICE_REQUIRED`。内部令牌由受控调用程序管理，不放入模型上下文或前端页面。

### PostgreSQL 后端

基本演示使用 `memory`。需要保存退货草稿和申请时，配置：

| 环境变量 | 配置内容 |
| --- | --- |
| `RETURN_DRAFT_STORE_BACKEND` | `postgres` |
| `BUSINESS_DATABASE_URL` | 例如 `jdbc:postgresql://127.0.0.1:5432/opspilot` |
| `POSTGRES_USER` | 本地数据库用户名 |
| `POSTGRES_PASSWORD` | 本地数据库密码 |

PostgreSQL 需要提前可用；切换后，启动时由 Flyway 执行迁移。该实现通过事务、行锁和唯一约束保护并发确认与幂等写入。内存实现通过同步方法串行执行读、判断、写入，不提供跨进程持久化。

## 7. 测试与常见问题

在 IDEA 中可以单独运行以下测试类：

- `OrderApiTest`：HTTP 查询、字段和错误状态。
- `ReturnEligibilityServiceTest`：7 天、15 天等资格边界。
- `ReturnReviewTest`：原因预审结论。
- `ReturnDraftTest`、`ReturnApplicationApiTest`：过期、取消、快照改变和确认幂等。
- `AuthenticationApiTest`：认证与订单访问权限。

使用 Maven 运行默认 Java 测试：

```powershell
mvn -B -ntp test
```

HTTP 测试使用随机端口，无需先手动启动 8081 服务。`PostgresReturnDraftIntegrationTest` 仅在 `RUN_POSTGRES_INTEGRATION=true` 时运行，并需要真实 PostgreSQL；默认跳过。测试覆盖范围不等于本次已经执行的验证结果，也不代表模型或整个 Agent 的效果。

| 现象 | 检查方式 |
| --- | --- |
| Java 版本不匹配 | 检查项目 SDK、运行配置 JRE 和 Maven 的 JDK 是否使用 17 |
| IDEA 没有识别依赖 | 确认 `pom.xml` 已作为 Maven 项目导入，查看 Maven 同步错误 |
| 8081 端口被占用 | 停止之前启动的 Java 实例，或在运行配置设置 `-Dserver.port=18081` 并同步修改演示地址 |
| 业务接口返回 503，但 `/health` 正常 | 检查认证配置；最小本机演示需显式设置 `OPSPILOT_AUTH_REQUIRED=false` |
| 业务接口返回 401 / 403 | 检查用户令牌与内部服务令牌两个请求头 |
| 查询其他用户订单返回 404 | 这是订单归属检查的预期行为 |
| 草稿返回 410 | 已超过一小时有效期，过期草稿不能用于首次确认申请 |
| 重启后草稿消失 | 当前为内存后端；需要持久化时使用 PostgreSQL |

演示验收顺序为：健康检查 → 查询订单 → 查询资格 → 查看草稿快照 → 明确确认 → 重试并比较申请编号。

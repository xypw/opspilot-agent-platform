# 第一步：Java 业务接口与 Python Agent 工具的边界

本节只解决一件事：Python 通过 HTTP 查询 Java 持有的订单。
这不是完整的售后 Agent，不迁移现有工单数据库，也不新增审批或退款。

## 当前范围

- Java：Spring Boot 3.5.16 / Java 17，提供订单查询和退货资格查询接口。
- 数据：三条明确虚构的内存订单，字段与原 `orders.py` 一致。
- Python：`order_service_client.py` 校验响应；`demo_java_order_query.py` 完成真实 HTTP 演示。
- 原 `/chat`、`/orders` 和 `tool_executor.py` **尚未切换**，仍保留原来的订单查询行为。
- 不读取 `.env`，不调用模型，不涉及真实客户信息。
- 服务只监听 `127.0.0.1:8081`；尚无鉴权，不允许直接公开部署。

## 一个请求如何运行

Python 客户端发送 GET 请求 → Java Controller 接收订单号 → Repository 查询模拟记录 →
Java 将 DTO 序列化为 JSON → Python 校验 JSON 并返回字典。

退货资格由 `GET /api/orders/{orderId}/return-eligibility` 查询。Java 按签收后的自然日
执行确定性规则：第 0～7 天允许无理由退货；第 8～15 天必须提供退货理由；第 16 天
起不能退货。未签收或已取消的订单也不能申请。边界包含第 7 天和第 15 天。

接口不用一个简单的 `true/false` 表达全部结果，而是返回三态 `decision`：
`NO_REASON_ALLOWED`、`REASON_REQUIRED`、`NOT_ALLOWED`。大模型只负责选择工具和向用户
解释结果，不能自行计算日期或更改 Java 给出的资格结论。

模型未来只会提出工具名和订单号；目标服务地址、权限和执行方式由程序控制。
本课并未让模型直接连接数据库，也没有让它生成任意 URL。

| Java HTTP 返回 | Python 客户端行为 |
| --- | --- |
| 200，合法订单且编号一致 | 返回 `id/status/product` 字典 |
| 404，且 `code` 为 `ORDER_NOT_FOUND` | 返回 `None`，表示确实查无订单 |
| 其他 404、400、403、500 等 | 抛出 `OrderServiceError` |
| 超时、无法连接、错误 JSON、返回别的订单 | 抛出 `OrderServiceError` |

不要把服务故障变成 `None`，否则 Agent 可能对用户说“订单不存在”。

## 关键文件

- `src/main/java/dev/opspilot/business/orders/OrderController.java`：HTTP 输入与状态码边界。
- `src/main/java/dev/opspilot/business/orders/DemoOrderRepository.java`：虚构的只读订单数据。
- `src/main/java/dev/opspilot/business/orders/OrderResponse.java`：Java 返回 DTO。
- `src/main/java/dev/opspilot/business/orders/ReturnEligibilityService.java`：确定性退货规则。
- `src/main/java/dev/opspilot/business/orders/ReturnEligibilityResponse.java`：规则结果契约。
- `../order_service_client.py`：Python 对 Java 的调用边界、超时/错误/响应校验。
- `../demo_java_order_query.py`：独立联调入口，不替换原 Agent 工具。

## 可复现验证

以下命令用于复现；教学时由老师执行，不要求学生重新安装工具。
从 OpsPilot 目录执行，有 Maven 时运行：

```powershell
mvn -f business-service/pom.xml package
java -jar business-service/target/business-service-0.1.0.jar
```

Java 在前台运行时，另一个终端从 OpsPilot 目录执行：

```powershell
.\.venv\Scripts\python.exe -X utf8 demo_java_order_query.py
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -p 'test_order*.py' -v
```

接口测试使用随机端口，不要求手动启动服务。当前 Java 测试共 12 项，Python 订单相关 14 项
（其中新增客户端测试 8 项）。这些测试不代表完整 Agent 的真实模型任务成功率。

## 本节练习：写工具适配函数，暂不修改默认入口

输入：`arguments_json` 是模型提出的工具参数字符串，例如 `{"order_id":"O-2003"}`；
`client` 是已经配置好 HTTP 地址和超时的 `JavaOrderClient` 对象。

请写 `query_order_via_java(arguments_json, client)` 的函数体，完成三个动作：

1. 用 `json.loads()` 将参数解析为字典。
2. 用已有的 `QueryOrderArgs.model_validate()` 校验并去掉编号首尾空白。
3. 把校验后的订单号传给 `client.get_by_id()`，返回查询结果。

不要写死订单号、不要让模型选择 URL，也不要捕获所有异常并返回 None。
学习者第一次尝试已经完成，方向正确；Review 后修正了两个点：应解析
`arguments_json`，并从 `model_validate()` 返回的对象中读取 `.order_id`。
修正后的适配函数位于 `../java_order_tool.py`，对应测试位于
`../tests/test_java_order_tool.py`。

下一小步已经把 `ORDER_QUERY` 注入点接入 `../tool_executor.py`：默认使用本地模拟
查询，真实联调时可替换成 `JavaOrderClient.get_by_id`。这让工具分发逻辑不需要知道
订单来自列表、Java 接口还是未来的数据库。

FastAPI 启动时由 `../order_query_factory.py` 读取 `ORDER_QUERY_BACKEND`：`memory`
保留离线实现，`java` 使用 `ORDER_SERVICE_URL`。未知后端会直接阻止启动，不会悄悄
返回模拟数据。`../scripts/verify_java_chat.py` 已验收 `/chat` 到 Java 的完整查询路径；
从 OpsPilot 根目录使用模块方式运行：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m scripts.verify_java_chat
```

验收：能说明 Java 接口与 Python 工具各自的职责，并完成这段适配函数。

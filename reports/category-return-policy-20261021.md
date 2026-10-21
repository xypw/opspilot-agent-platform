# 分类售后规则修改验证（2026-10-21）

本页保留侧对话完成时的验证快照。后续V3/V4迁移、真PG集成和容器部署已经完成，最新范围与结果见 [主链路验收](persistent-stack-20261021.md)；下文的“未部署”指本页记录的历史节点。

## 修改结果

- Java 改为依据可信商品事实和下单承诺判断，不再统一“7天无理由、8—15天需原因、15天后拒绝”。
- 普通商品至少7天，更有利的商家承诺生效；类别、拆封/激活、商品完好性、购买确认分别检查。
- 质量/其他待核实原因进入人工证据核实状态；不把提出质量问题视为证据已核实，也不按统一15天拒绝。
- 政策事实未知返回待核实，不能默认普通商品；购买确认缺失不能直接作为拒绝依据。
- 草稿绑定商品政策指纹；政策变化要求重新获取草稿并确认；质量核实状态不能被另一次确认绕过；原申请重试仍幂等。
- Python 资格/预审协议同步扩展，保留旧响应兼容但不再产生旧的统一15天决策。

官方来源、规则表、演示编号及未覆盖范围见 [分类售后规则](../docs/category-return-policy.md)。商品事实来自内存明确样例或 PG 政策表，不从名称、用户提示或PDF猜测。

## 验证

| 检查 | 结果 | 范围 |
|---|---|---|
| Java Maven 回归 | 56项：54通过，2跳过，0失败/错误 | 分类策略、真实本机随机端口HTTP、身份校验、草稿过期、并发幂等、政策变更旧确认失效 |
| 新Java分类用例 | 18项规则测试＋4项HTTP测试通过 | 生鲜/定制/报刊、数字商品与健康类拆封、激活、确认缺失、合理查验、承诺延长、未知事实、用户伪造属性、人工核实阻断 |
| Python相关回归 | 38项通过 | 新分类契约、订单客户端、预审客户端、原因补全、service API；HTTP替身，不调用外部模型 |
| Git diff空白检查 | 通过 | 现有CRLF提示不属于代码错误 |
| PostgreSQL集成 | 未执行 | Docker引擎未运行，2项显式条件测试跳过；V4政策迁移未应用到现有数据库 |

Java初轮发现Spring为final的@Repository创建类代理失败，移除相关仓库的final后，HTTP与完整Java回归通过。复用了本机Maven依赖缓存，没有下载依赖。

复现命令（业务服务目录，有Maven时）：

```powershell
mvn -o -Dmaven.repo.local=../.maven-cache test
```

Python命令（项目根目录，使用内存后端并清空REDIS_URL）：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_category_return_contract tests.test_order_service_client tests.test_return_review_client tests.test_return_reason_flow tests.test_service_api -q
```

JUnit逐套结果保存在 `business-service/target/surefire-reports/`，属于本机生成产物。

## 交付边界

本轮只修改分类售后规则及必需的跨服务契约、测试和文档，保留主对话的订单持久化、混合检索改动。没有启动Docker、运行数据库迁移、更新容器、提交/推送Git或调用付费模型。现有运行环境需在主对话完成迁移和部署后才能使用本轮代码。

迁移顺序为V2→V3→V4；V4新增 `order_return_policies` 和草稿 `policy_fingerprint`，只增加新的虚构演示记录，不覆盖历史订单。原有未完成草稿缺少政策指纹，按新规则要求重新确认；已完成申请仍返回原回执。人工核实目前为服务端状态及阻断，不包含完整客服工单派发/审核台。

---
name: opspilot-regression
description: 为 OpsPilot 的 Python FastAPI、LangGraph、RAG 与 Java Spring 业务服务选择、运行并解释回归检查。适用于本仓库完成功能修改或代码审查之后；不用于判断真实模型效果或外部服务可用性。
---

# OpsPilot 回归验证

根据本次改动涉及的运行边界选择检查，并准确说明验证结果能够证明什么。

## 选择验证范围

- 只修改一个独立Python模块：先运行对应的 `unittest` 测试模块。
- 修改Python共享入口、图状态、工具契约、存储或RAG：先运行相关测试，再运行Python全量测试。
- 修改Java业务规则、Controller或DTO：运行相关Java测试类；共享服务或契约变化时运行Java全量测试。
- 修改Python与Java之间的契约：两端都运行相关测试。模拟HTTP测试只能证明序列化和错误映射，不能证明真实服务已经连通。
- 只修改文档：执行 `git diff --check`，检查链接、事实描述和意外泄露的敏感信息。没有运行时原因时无需执行应用测试。

## 隔离Python测试环境

在仓库根目录使用项目虚拟环境运行。覆盖本地服务配置，避免普通测试依赖用户的 `.env`、Redis、PostgreSQL或API Key：

```powershell
$env:REDIS_URL=' '
$env:TICKET_REPOSITORY_BACKEND='memory'
$env:ORDER_QUERY_BACKEND='memory'
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests
```

需要针对性验证时，把 `discover -s tests` 替换为明确的测试模块，例如
`tests.test_return_reason_flow tests.test_return_draft_client`.

## 验证Java代码

在 `business-service/` 目录运行。优先使用仓库或当前环境已经配置的Maven；只有所需依赖已经存在于本地时才使用离线模式：

```powershell
mvn -B -ntp test
```

针对性验证可使用 `-Dtest=ClassOne,ClassTwo test`。不得把某位开发者电脑上的IDE绝对路径写进仓库文件。

## 分析失败

修改代码前，先阅读最后一个异常和第一个与项目有关的调用栈位置。判断错误属于业务实现、接口契约、环境或依赖，还是测试替身。测试替身出错时应保留正确的生产边界；例如测试重复使用已关闭的HTTP Client时，不应因此修改生产网关。

Mock测试只能证明确定性的控制流程。被跳过的集成测试、模拟模型调用和固定测试数据，不能证明Redis或PostgreSQL真实连通，也不能证明真实模型准确率。

## 完成条件

相关测试通过，并且需要执行的全量测试也通过后结束验证。报告通过、失败和跳过数量，说明尚未验证的外部依赖及重要限制。不得暴露 `.env` 内容；本Skill不执行Git提交。

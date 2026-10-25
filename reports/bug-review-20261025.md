# 2026-10-25 门户、模型响应与交付检查

## 范围

收拢此前尚未提交的账号门户、图片问答、本地 Ollama、会话上下文与恢复功能，并审查其边界。本轮未修改 Java 业务规则或 TracePilot，未调用本地/云端大模型，未下载依赖，未重建或部署应用。

## 已复现并修复

| 问题 | 修复与证据 |
| --- | --- |
| 门户先校验草稿再取得执行权，并发刷新可能使旧批准作用于新草稿 | `portal_api.py` 先 `claim_action`，再读取并校验当前 `draft_id`；`tests/test_customer_portal.py` 注入刷新竞争，旧快照返回 409 且不调用恢复执行 |
| 等待很久的草稿开始确认时未刷新活动时间，可能被同步入口当作过期任务；迟到同步可覆盖新操作 | `portal_store.py` 在取得执行权时更新 turn 时间并验证归属；同步完成使用时间和活动任务的比较更新，状态变化则拒绝覆盖；18 项真实 PG 集成包含这两个场景 |
| 退出登录后输入框和文件选择残留，后续账号可能看到未发送内容 | `static/portal.js` 退出时清除文本、文件、密码及外发授权；Node DOM 替身用例验证清除，不代表真实浏览器端到端验收 |
| 云端视觉输出已截断/被过滤，但内容恰好是有效 JSON 时仍可能接受；畸形响应结构异常不统一 | `vision_service.py` 校验完成原因、消息结构与工具字段；3 项测试覆盖截断、过滤、异常 envelope 和正常输出，均使用模拟 HTTP |
| 分词资产是合法 JSON 但结构错误时抛出未归一异常，门户可能锁在结果未知 | `qwen_tokenizer.py` 校验 manifest 类型，解析同一份已验指纹的字节并归一为预算前置错误；3 项回归验证零 HTTP、零配额及门户释放活动状态 |
| 重新运行上下文验收可能覆盖历史报告，多个进程可能同时使用同一预算账本 | 验收先独占报告路径/账本锁，再执行检查；保留原 12 次预算，不重置批次或限流 scope；新增 11 项 mock 测试验证覆盖拒绝、并发拒绝与失败不退还已扣额度 |

分词异常测试最初使用方法装饰器管理环境变量，而内嵌门户 fixture 在稍后清理时又恢复了临时模型配置，造成全量测试顺序依赖。已改为 `setUp/enterContext` 按 LIFO 清理；重新运行全量通过。此问题属于测试隔离，不能当作模型质量改进。

## 本轮结果

| 检查 | 结果 | 边界 |
| --- | --- | --- |
| Python `unittest discover -s tests` | 682 项：653 通过、29 默认跳过、0 失败 | 跳过为显式依赖开关控制的 PG/Redis 集成；非真实模型评测 |
| Node 三个页面测试文件 | 31/31 通过 | 模拟 HTTP/DOM，非浏览器实机验收 |
| `tests.test_portal_postgres_integration` | 18/18 通过、0 跳过 | 本机 PostgreSQL；逐项随机 schema，结束仅清理该测试 schema |
| Agent 固定任务回归 | 8/8，通过基线差异检查 | 隔离内存服务、模拟模型；模型 HTTP 尝试均为 0 |
| Git 差异格式检查 | 通过 | 不等于不存在其他缺陷 |

固定任务逐例结果：[agent-mock-bug-review-20261025.json](agent-mock-bug-review-20261025.json)。测试集合有交叉，不将这些数量相加作为独立场景总量。

真实 PG 检查只临时启动原先停止的数据库容器；结束后恢复为停止状态，保留原数据卷。API、Java 和 Redis 应用未重新启动，本轮改动尚未部署。GitHub CI 只有推送后才能触发，不能用本地通过代替远端状态。

## 复现

在项目虚拟环境内运行，先隔离本机 `.env` 的业务与模型配置：

```powershell
$env:REDIS_URL=' '
$env:TICKET_REPOSITORY_BACKEND='memory'
$env:ORDER_QUERY_BACKEND='memory'
$env:KNOWLEDGE_STORE_BACKEND='memory'
$env:OPSPILOT_AUTH_REQUIRED='false'
$env:OPSPILOT_MODEL_PROVIDER='zhipu'
$env:MODEL_RATE_LIMIT_REQUESTS='0'
$env:SILICONFLOW_API_KEY=' '
$env:RUN_REDIS_INTEGRATION='0'
$env:RUN_REDIS_RATE_LIMIT_INTEGRATION='0'
$env:RUN_POSTGRES_INTEGRATION='0'
$env:RUN_PORTAL_POSTGRES_INTEGRATION='0'
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests
node --test static/demo.test.cjs static/service.test.cjs static/portal.test.cjs
.\.venv\Scripts\python.exe scripts/run_agent_evaluation.py --mode mock --runtime isolated --output output/evaluation/current-agent-evaluation.json
.\.venv\Scripts\python.exe scripts/check_agent_regression.py --baseline reports/agent-v2-offline-20260923.json --current output/evaluation/current-agent-evaluation.json
```

真实 PG 集成另行显式开启 `RUN_PORTAL_POSTGRES_INTEGRATION=1`，用本机 `DATABASE_URL` 运行 `python -m unittest tests.test_portal_postgres_integration`；不要把数据库密码写入报告。需要测试库权限，不针对远程生产库运行。

## 简历与未覆盖项

简历只补充已存在的账号/历史、多模态、本地模型、有界上下文与源文追溯，不改个人信息、毕业年份及 TracePilot 原表述；历史新冻结集 16/20、旧回归 20/20 保持原口径。一页 DOCX 已本地渲染并逐页检查，再交付对应 PDF，个人文件不进入仓库。

本次审查不是完整渗透测试，也不能证明全部并发故障已消除；未新增真实模型任务成功率。图片 token 仍需后端超窗拒绝兜底，摘要仍可能损失信息，会话内语义召回不是跨会话长期记忆。应用部署和真实浏览器验收未在本轮重跑。

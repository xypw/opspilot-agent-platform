# 2026-09-25 部署与验收

## 最新结果：9/26 格式纠正与问题粒度修正已部署

最后镜像为 `sha256:a36fa4929b6e934845aea25dd06245b2d3665c613ba89d8aaa297b531bcf4f4f`，前一版本保留为 `opspilot-api:before-format-retry-20260926`。最终 `evidence_review.py` 源码 SHA-256 为 `dc5725fd4c1d77abffdd09b09137f3ad8c956146161cf4e1011b79f465077e75`，已在容器内核对。

- 仅 API 再次使用缓存重建；Java、数据库、Redis 和数据卷保持不变，四个容器均 healthy。
- 最终镜像业务冒烟再次通过：页面、鉴权/订单归属、错误快照 409、重复确认返回同一申请、共享配额和过期恢复。仍使用原 SUBMITTED 演示草稿，未冒称首次创建。
- 最终镜像内证据审查/失败诊断/格式纠正测试 **37/37**，真实 Redis 集成 **4/4** 通过；模型请求均被模拟。
- 最终源码完整离线回归 **469 项，462 通过、7 跳过**。下面记录的 28 项容器测试和 460 项离线测试是前一阶段，不与本阶段相加。
- 真实模型另行复测仍有 h09 未验收通过；细节见 [四例后续处理](failure-followup-20260925.md)。部署可用不等于所有模型效果问题已解决。

## 9/25 第一轮：用户恢复 Docker 后部署通过

已部署代码提交 `482c59b17811fab59cb846cd33294211f4ec7d8c`。仅重建 API 应用，依赖安装使用缓存；保留原鉴权配置，Java、PostgreSQL、Redis 容器及数据卷未重建。四个服务均为 healthy。

- 新 API 镜像：`sha256:98e6fd746e56553777f5fe4d931694f2974371f42425c8917cbb96b37be8a68f`。
- 旧镜像保留为 `opspilot-api:before-evidence-20260925`，原 ID 为 `sha256:e91c1d4649ff827f6a67c951e34c4db3dc454676a5f2754a082e658524b6081c`。
- 容器内 `evidence_review.py`、`evidence_support.py`、`evidence_holdout_evaluation.py` 的 SHA-256 与本地已提交源码一致。
- 本机入口：`http://127.0.0.1:8011/demo`；业务页面：`http://127.0.0.1:8011/service`。

| 本轮验证 | 结果与范围 |
| --- | --- |
| `/health`、`/demo`、`/service`、`/openapi.json` | HTTP 200 |
| 未认证查询 / 本人订单 / 他人订单 | 分别返回 401 / 200 / 404 |
| 错误金额快照 | 409，原草稿状态不变 |
| 重复确认 | 两次返回同一申请；测试开始时演示草稿已为 SUBMITTED，只验证原申请复用，不声称本轮完成首次提交 |
| 重试与证据审查共享 Redis 配额 | MockTransport 模拟一次 429 后重试成功，证据审查后计数为 3，第 4 次在发送 HTTP 前被阻止 |
| Redis 窗口过期恢复 | 通过，使用独立测试 key，结束后只删除该 key |
| 新镜像内证据审查与失败诊断测试 | 28/28 通过，模型被替代，不调用供应商 |
| 新镜像连接真实 Redis 的集成测试 | 4/4 通过，含两进程共享预算及真实过期行为 |
| 页面 JavaScript 测试 | 4/4 通过 |
| 固定任务回归 | mock/isolated 8/8，基线比较无退化；原始报告见 [本轮模拟任务](agent-offline-deployment-20260925.json) |

部署验收的外部模型请求为 0。独立真实模型复测与部署验收分别统计，见 [四例后续处理](failure-followup-20260925.md)。上述结果不能当作真实模型成功率或生产性能证明。

复现（需先配置鉴权并启动已有服务；写入授权只针对虚构演示订单）：

```powershell
docker exec opspilot-api python scripts/smoke_deployed_runtime.py --allow-demo-write
node --test static/demo.test.cjs static/service.test.cjs
.\.venv\Scripts\python.exe -X utf8 scripts/run_agent_evaluation.py --mode mock --runtime isolated --output output/evaluation/<新报告名>.json
.\.venv\Scripts\python.exe -X utf8 scripts/check_agent_regression.py --baseline reports/agent-v2-offline-20260923.json --current output/evaluation/<新报告名>.json
```

## 早先阻塞记录（已由上述验收更新）

本轮候选代码离线回归共 460 项，453 通过、7 项集成条件跳过。应用镜像未构建、容器未更新，未进行部署后业务验收；不能将 9/24 的验收记录视为本轮代码已经上线。

## 阻塞与安全恢复

Docker Desktop 4.89.0 启动日志先报告 `sailor-ingest.sock` 改名失败，随后报告 `docker-secrets-engine/engine.sock` 改名失败，均为 `The file cannot be accessed by the system`。引擎不可用，因此停止后续应用部署和发布。

已进行的恢复尝试：

1. 确认 Docker 停止，将 `%LOCALAPPDATA%/Docker/run` 改名为 `run-backup-20260925-recovery`，保留通信文件备份后启动。
2. 第二处错误出现后，核对 `%LOCALAPPDATA%/docker-secrets-engine` 仅包含两个零字节 socket 端点，没有读取凭据内容。
3. 关闭已崩溃的 Docker 进程，将新建 `run` 与上述 socket 目录分别保留为 `run-backup-20260925-recovery2`、`docker-secrets-engine-backup-20260925-recovery2`，再启动；仍出现同一 Secrets Engine 错误。

没有重置 Docker、删除数据、修改数据库卷、重建基础设施或更改 Windows 安全配置。备份均保留，恢复尝试未解决引擎问题。后续操作若涉及重启 Windows、升级/重装 Docker 或系统设置，应先获得用户确认并保护现有卷。

## 恢复后验收清单

- `docker ps` 能列出原有容器，记录并保留旧 API 镜像及已有鉴权配置。
- 仅更新 API 应用；复用 Java、PostgreSQL、Redis、数据卷和构建缓存。
- 检查 `/health`、`/demo`、`/service`，运行 `scripts/smoke_deployed_runtime.py --allow-demo-write`；只操作虚构演示订单，不使用真实模型。
- 完成部署后记录新镜像及验收结果，再核对远端并正常推送；不强制推送。

真实模型复测结果见 [四例后续处理](failure-followup-20260925.md)，与部署状态分别记录。

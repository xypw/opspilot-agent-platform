# 2026-09-25 部署尝试：Docker 引擎未恢复

## 结果

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

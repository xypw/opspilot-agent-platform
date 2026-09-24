# 2026-09-24 本机部署验收

## 部署范围

仅通过现有构建缓存重建并替换 API 应用；Java、PostgreSQL、Redis 和原有数据卷保持不变，沿用现有鉴权配置。更新前镜像保留为 `opspilot-api:before-runtime-20260924`，未清空数据库或 Redis。没有请求外部模型。

本机入口：`http://127.0.0.1:8011/demo`；业务页面：`http://127.0.0.1:8011/service`。

## 实测结果

| 验证对象 | 结果 |
| --- | --- |
| `/health`、`/demo`、`/service`、`/openapi.json` | 均为 HTTP 200 |
| 未认证查询订单 | HTTP 401 |
| 当前用户订单、他人订单 | 分别为 HTTP 200、404 |
| 演示草稿补充原因、错误金额确认 | 正确进入 REVIEWED；错误金额返回 409，状态不改变 |
| 同一草稿两次确认 | 返回同一申请，首次确认前草稿为 REVIEWED |
| 重试与证据审查共享 Redis 配额 | MockTransport 首次 429、重试成功及证据审查共占 3 次；第 4 次在发出 HTTP 前被阻断 |
| 配额窗口到期 | 到期后再次放行；只使用 UUID 测试 key，完成后删除该 key |
| 离线 Python 回归 | 共 449 项，442 通过、7 项集成测试跳过 |
| 页面 JavaScript 测试 | 4/4 通过 |

本次创建并保留虚构订单 O-2001 的演示申请，不涉及真实客户。重复执行会复用已有申请，因此已有 SUBMITTED 草稿时脚本只验证复用，不重新声称覆盖首次提交。

## 复现

部署须配置 `.env.example` 所列鉴权项，不能以空环境重建而覆盖已有身份设置。在 API 容器中执行：

```powershell
docker compose exec -T api python scripts/smoke_deployed_runtime.py --allow-demo-write
```

脚本只对本机 API 和 Redis 发起请求，模型使用 HTTP MockTransport。`--allow-demo-write` 明确允许确认虚构演示订单；脚本不打印令牌和连接密钥。跨进程原子限额测试的结果和命令另见 [运行边界验证](runtime-boundaries-20260924.md)。本次额外在更新后的 API 容器内复测该组 4 项 Redis 集成测试。

生产镜像排除了 `tests` 目录，容器内额外复测时只复制一个测试文件到 `/tmp`：

```powershell
docker cp tests/test_model_rate_limit.py opspilot-api:/tmp/test_model_rate_limit.py
docker exec -e PYTHONPATH=/app:/tmp -e RUN_REDIS_RATE_LIMIT_INTEGRATION=1 -e RATE_LIMIT_TEST_REDIS_URL=redis://redis:6379/0 opspilot-api python -m unittest test_model_rate_limit.RedisRateLimitIntegrationTests -q
```

这些结果验证部署、身份、业务写入和请求限额，不是模型效果或吞吐量基准。

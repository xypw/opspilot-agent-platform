# 订单持久化、分类政策与持久化混合检索验收

日期：2026-10-21。业务数据均为虚构，本轮外部模型 HTTP 调用为0。

## 实现与部署

- Java 从 PostgreSQL 查询订单归属、金额和签收日；草稿操作先锁订单再锁草稿，处于同一事务。政策读取加共享锁，草稿保存政策指纹。
- 合入商品类别、完好性、下单确认和商家承诺判断。质量问题进入核实，缺失商品事实不默认批准。订单/政策变化使旧确认失效，人工核实不能通过确认接口绕过。
- 已成功提交的同一 Agent 确认返回原申请；Java响应丢失时继续已获批准的保存节点，沿用同一draft_id。取消不能撤销已提交申请。
- PG在库内执行词片数组/GIN与pgvector两路有限召回；两路均执行准入，再做RRF和可配置重排。关键词不是BM25。重排暂不可用时使用已有RRF结果，程序错误不被吞掉。
- 直接发布、按页审核发布和历史回填生成相同词片。待审核PDF不进入索引，片段编号、页码、原文位置和标题路径保留。

## 实际验证

| 检查 | 结果 | 范围 |
|---|---|---|
| Python完整离线回归 | 531项：520通过、11集成默认跳过 | API、授权、证据、分类契约、检索及确认恢复 |
| Python真PG集成 | 4/4通过 | 双路检索、持久化重读、按页审核隔离、分批回填与向量保留；固定向量和假重排器 |
| Java内存/HTTP回归 | 54通过、12 PG集成默认跳过 | 分类策略、真实本机HTTP、身份、草稿及业务规则 |
| Java真PG集成 | 12/12通过 | 空库/旧库迁移、数据保留、订单/政策锁、快照变化、幂等及回滚 |
| Redis集成 | 5/5通过 | 跨进程共享配额、真实过期、缺TTL处理、对象重建后恢复检查点；独立测试key |
| 页面单测 | 4/4通过 | 确认按钮、快照和请求路径 |
| 冻结模拟任务 | 8/8、回归门禁通过 | 保留原v2案例口径，不作为新分类策略或真实模型效果 |
| 最终部署HTTP验收 | 22/22通过 | 真API/Java/PG/Redis、模拟模型；同名JSON保留检查列表 |

Python默认跳过的11项中，新PG4项、Redis5项已单独显式运行；旧PostgreSQL工单2项未运行，其对应教学接口在鉴权部署中关闭。

部署检查覆盖匿名拒绝、跨用户订单/任务隔离、Java拒绝缺内部凭据的请求、确认前零申请、检查点读取、提交与重复确认、持久化引用、订单和政策联合查询、订单金额变化拒绝旧确认、刷新草稿、新金额提交，以及普通/生鲜/未知政策质量问题的核实和提交阻断。

## 数据与恢复

- 迁移前数据库备份位于已忽略的 `output/backups`，不进入Git。
- 迁移前3条旧订单、1条申请、2份文档、4个知识片段。迁移后旧数据保留，新增5条有归属的虚构演示订单；原申请仍1条、文档仍2份、片段仍4个。4个已发布片段补齐词片，重复回填更新0条。
- 集成使用UUID独立schema；部署验收使用5条带唯一nonce的临时订单。结束后临时订单与测试schema均已清理，原演示订单未用于试提交。
- Java V3/V4已应用，V2未改写。旧订单缺失归属/金额不猜测回填；未完成的老草稿缺政策指纹时需刷新，已完成申请保留回执。
- 旧镜像备份：`opspilot-api:before-persistent-ab-20261021`、`opspilot-business-service:before-persistent-ab-20261021`。
- 最终API镜像：`sha256:b9dcb6dac25bbde939d0c17a9488b64725e3082df411ca4980f8a7624a3cb137`。
- 最终Java镜像：`sha256:436253c70e2ff8102658926a22737a2b08c0ba54ff1438cad6ba3036766d0951`。
- Docker启动因两个通信目录内残留socket无法重命名而失败；保留目录备份后重新创建通信文件恢复。未重置容器、镜像或数据卷。
- 本机.env原先没有持久化身份凭据，已备份配置并初始化演示身份；Compose空鉴权变量现在直接报错。旧页面令牌需更新，凭据只保存在本机忽略文件。

## 复现

先配置本机数据库并启动PostgreSQL、Redis，使用项目虚拟环境：

```powershell
python scripts/migrate_hybrid_search.py
$env:RUN_POSTGRES_INTEGRATION='true'
python -m unittest tests.test_postgres_hybrid_integration -v
python scripts/run_java_postgres_tests.py --maven '本机已有Maven可执行文件'
```

Java测试脚本只接受本机数据库，以独立schema向Spring注入明确测试配置，结束后删除本轮schema；Maven使用已有缓存并保持离线。

本轮核验Python依赖与原镜像的requirements SHA-256相同，因此复用旧运行镜像、本地向量缓存及离线jar；无大规模下载。缓存构建只适用于依赖未变，常规构建仍使用原Dockerfile：

```powershell
# business-service目录先打包，再回项目根目录执行缓存构建：
mvn -o -Dmaven.repo.local=../.maven-cache -DskipTests package
$env:OPSPILOT_API_BASE_IMAGE='opspilot-api:before-persistent-ab-20261021'
$env:OPSPILOT_JAVA_BASE_IMAGE='opspilot-business-service:before-persistent-ab-20261021'
docker compose -f docker-compose.yml -f docker-compose.cached.yml build api business-service
docker compose up -d --no-deps --no-build --pull never --wait business-service api
python scripts/smoke_persistent_stack.py
```

入口： http://127.0.0.1:8011/demo 与 http://127.0.0.1:8011/service。

## 边界

本轮证明持久化、检索接线、分类控制、事务与部署行为，未测新增真实模型或真实重排效果。历史证据审查20/20与16/20未改写。真实联合任务和检索消融需按单独数据与调用预算运行。本页记录本机验收快照；后续提交与远端发布状态以 Git 历史为准。

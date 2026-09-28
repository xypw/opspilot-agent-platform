# PDF 入库审核与发布

## 边界

上传接口先解析 PDF，再检查页面风险、切块来源位置与跨边界的关键字段。全部页面通过保守校验且有文字片段时，才直接发布。任一页面需要 OCR 或人工核对时，整份文档进入待审核区；此时不生成向量，正式知识检索与 Agent 工具都不会读取它。识别风险不等于证明语义正确，对高风险制度文件仍须人工对照原 PDF。

## 审核步骤

以下接口均要求 `knowledge_admin` 身份：

1. `POST /documents/upload` 上传 PDF。响应中 `publication_status=pending_review` 表示没有发布任何片段。待审核结果保存解析页、页码、切块、原文偏移、解析版本、失败原因及原 PDF 原件；内存模式只适合本机演示，重启后待审核记录和原件都会丢失。
2. `GET /documents/{document_id}/review` 查看提取文本、片段和风险标记；`GET /documents/{document_id}/source` 下载原 PDF，人工并排核对条件、金额、否定词及阅读顺序。
3. `POST /documents/{document_id}/publish` 提交如 `{"pages":[1,3]}`。只有明确列出的、拥有文字片段的页面被发布；其余页面仍留在待审核区。已经发布的页面不能重复批准，空白或仍需 OCR 且无片段的页面不能发布。

发布操作把片段写入业务库的单个事务，并同时记录审核人和已批准页。PostgreSQL 检索查询显式过滤未发布状态。与 PDF 原文一致的偏移只能证明片段来自当前提取文本，不能证明提取文本与原 PDF 视觉语义一致。

## 质量标记及限制

- `possible_garbled_text`：提取文本有较高比例的替换字符或控制字符。
- `unusually_short_text`、`needs_ocr`：文字异常少或无可提取文字。
- `possible_reading_order_error`、`possible_two_column_layout`：提取片段的坐标顺序出现可疑跨列或回跳。PDF 坐标可能不可靠，因此只标风险。
- `possible_table_or_columns`、`mixed_image_text_layout`：表格、多栏或图片混排风险。
- `possible_cross_page_structure`：连续页面疑似切断表格或条件句，整份文档先隔离核对。
- `source_span_mismatch`、`critical_field_split`、`possible_condition_boundary_split`：片段定位失败，或期限、金额、否定条件在片段边界附近被拆开。

这些规则可能误报，也可能漏掉无法从提取文本看出的错误。新增规则或解析器版本后，应重新人工核验双栏错序、跨页表格、扫描与文字混合、金额、期限和否定词样本，并测试未批准片段不可检索。不得把“通过结构检查”表述为“PDF 语义已获保证”。

## 本机集成验收

`scripts/smoke_document_review.py` 使用虚构政策 PDF 加一页空白页，验证未认证请求被拒、待审核文档不进入检索、原件可供管理员核对、批准页面可检索、重复批准和空白页发布被阻止。脚本使用随机文档编号，最后只删除自身创建的测试记录，不调用付费模型。验收中曾发现两处真实问题：普通/紧急退款规则混在一个片段里，以及 PDF 提取的“3 小时”未被时间证据规则识别；现已分别用规则行切块和可选空白匹配修复，并有单元回归用例。

## 已有数据库迁移

`db/init.sql` 只在空卷首次启动时执行。已有数据卷先备份，再执行 `db/migrations/001_document_review.sql`，确认新增列与 `document_review_queue` 表存在后再更新 API。迁移将旧文档视为既有已发布内容，但无法补齐它们的原 PDF、来源位置或人工审核记录；需要可信审核的旧文档应从原件重新上传并复核。迁移本身不启动 Docker，也不调用模型。

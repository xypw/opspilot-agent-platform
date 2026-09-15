# 证据充分性规则：人工边界集基线

用 `evaluation_data/evidence_sufficiency_cases.json` 中 15 条**人工构造**的“问题—单个候选片段—人工标签”
测试 `filter_evidence_for_question()`。可在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/evaluate_evidence_sufficiency.py
```

本次结果：TP=6、FN=0、FP=6、TN=3。逐例结果由脚本输出；以下 6 条当前误放行：

| 用例 ID | 缺少的回答证据 |
| --- | --- |
| `refund_eta_from_steps` | 有申请步骤，但没有到账时间 |
| `invoice_destination_from_eligibility` | 说明可申请，但没有发送位置 |
| `overtime_from_refund` | 主题完全不同 |
| `refund_where_from_eta` | 没有申请入口 |
| `cancel_order_from_refund_steps` | 退款步骤不是取消订单步骤 |
| `invoice_steps_from_destination` | 邮箱用途不是申请步骤 |

这是**固定候选片段的第二道证据规则测试**：没有执行 Embedding、向量检索、相似度门槛、Reranker、
LLM 回答或真实四服务链路。样本故意覆盖薄弱边界，不是随机生产流量，因此不能把 6/15 或
6/9 写成项目真实准确率/误放行率。它只说明目前的关键缺口是“片段含相关词”与“片段包含回答所需事实”
尚未可靠地区分，尤其对非流程型问题。下一步应设计可核查的答案证据字段或更强的充分性判断，
并用同一标注集做改前改后对比；不能靠添加无穷多条正则宣称解决。

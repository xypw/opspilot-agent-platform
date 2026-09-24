"""业务证据边界回归；样本与标签独立于生产规则文件。"""
import json
from pathlib import Path
import unittest

from evidence_support import (
    assess_evidence_for_question,
    classify_evidence_for_question,
    filter_evidence_for_question,
    partition_evidence_for_question,
)


class EvidencePolicyRegressionTests(unittest.TestCase):
    def test_99_frozen_business_cases(self):
        data = Path(__file__).resolve().parents[1] / "evaluation_data"
        count = 0
        for name in ("evidence_sufficiency_cases.json", "evidence_sufficiency_challenge_cases.json",
                     "evidence_holdout_cases.json", "evidence_boundary_cases_v3.json"):
            for case in json.loads((data / name).read_text(encoding="utf8")):
                content = case.get("candidate_content", case.get("candidate", {}).get("content", ""))
                with self.subTest(dataset=name, case=case["case_id"]):
                    decision = assess_evidence_for_question(case["question"], content)
                    self.assertEqual(decision.decision == "admit", case["sufficient"], decision)
                count += 1
        self.assertEqual(count, 99)

    def test_unknown_topic_never_defaults_to_admit(self):
        assessment = assess_evidence_for_question("那件事还有什么要求？", "退款三个工作日到账。")
        self.assertEqual(assessment.decision, "uncertain")
        self.assertEqual(assessment.reason, "unknown_question_topic")

    def test_unknown_requirement_is_not_satisfied_by_empty_set(self):
        self.assertEqual(classify_evidence_for_question("退款为什么停了？", "退款三天到账。"), "uncertain")

    def test_missing_identifier_is_hard_conflict_even_with_unknown_topic(self):
        assessment = assess_evidence_for_question("ORDER_207 表示什么？", "该错误表示重复提交。")
        self.assertEqual(assessment.decision, "reject")
        self.assertEqual(assessment.reason, "identifier_conflict")

    def test_metadata_does_not_authorize_mismatched_evidence(self):
        evidence = [{"chunk_id": "correct-answer", "title": "退货资格", "score": 1.0,
                     "content": "商品故障时可以申请两年保修。"}]
        self.assertEqual(filter_evidence_for_question("签收商品后可以退吗？", evidence), [])

    def test_offline_and_partition_paths_agree_on_ambiguous_evidence(self):
        question = "如何退款？"
        candidates = [{"chunk_id": "ambiguous", "content": "通过售后入口发起请求并补充原因后确认。"}]
        self.assertEqual(partition_evidence_for_question(question, candidates)["uncertain"], candidates)
        self.assertEqual(filter_evidence_for_question(question, candidates), [])

    def test_same_scope_can_supply_compound_facts_in_two_sentences(self):
        self.assertEqual(classify_evidence_for_question(
            "退款多久，退到哪里？", "退款三日内到账。退款原路退回。"
        ), "admit")

    def test_neighboring_priority_does_not_supply_missing_time(self):
        self.assertNotEqual(classify_evidence_for_question(
            "高优先级工单多久响应？", "高优先级工单正在处理中，普通工单一天内响应。"
        ), "admit")

    def test_neighboring_identifier_does_not_supply_missing_meaning(self):
        self.assertNotEqual(classify_evidence_for_question(
            "PAYMENT_207 表示什么？", "PAYMENT_207 还在排查，PAYMENT_500 表示支付服务不可用。"
        ), "admit")

    def test_continuation_keeps_subject_and_identifier_context(self):
        for query, text in [
            ("高优先级工单多久响应？", "高优先级工单，应在二十分钟内响应。"),
            ("PAYMENT_210 表示什么？", "PAYMENT_210 指的是订单已取消导致支付失败。"),
        ]:
            with self.subTest(query=query):
                self.assertEqual(classify_evidence_for_question(query, text), "admit")


if __name__ == "__main__":
    unittest.main()

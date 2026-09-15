"""流程型问题的保守证据准入测试；不调用模型。"""

import unittest

from evidence_support import filter_evidence_for_question


class EvidenceSupportTests(unittest.TestCase):
    def test_refund_timing_does_not_answer_refund_steps(self):
        timing = [{"chunk_id": "timing", "content": "退款审核通过后三个工作日到账。"}]

        self.assertEqual(filter_evidence_for_question("我应该怎么退款", timing), [])
        self.assertEqual(filter_evidence_for_question("退款多久到账", timing), timing)

    def test_submitted_status_is_not_an_instruction(self):
        status = [{
            "chunk_id": "status",
            "content": "退款申请已提交，审核通过后通常三个工作日到账。",
        }]

        self.assertEqual(filter_evidence_for_question("如何申请退款", status), [])

    def test_submitting_then_arrival_is_not_a_procedure(self):
        status = [{
            "chunk_id": "high-score-status",
            "content": "客户提交退款申请后，通常三个工作日到账。",
            "score": 0.888,
        }]

        self.assertEqual(filter_evidence_for_question("我应该怎么退款", status), [])
        self.assertEqual(filter_evidence_for_question("退款多久到账", status), status)

    def test_explicit_submit_instruction_is_procedure(self):
        instruction = [{"chunk_id": "submit", "content": "请提交退款申请。"}]

        self.assertEqual(
            filter_evidence_for_question("我应该怎么退款", instruction), instruction
        )

    def test_offline_refund_instruction_without_ui_verbs_is_procedure(self):
        instruction = [{
            "chunk_id": "counter-step",
            "content": "携带订单号和商品至售后服务台办理退款。",
        }]

        self.assertEqual(
            filter_evidence_for_question("如何申请退款", instruction), instruction
        )

    def test_completed_counter_visit_is_not_a_procedure(self):
        status = [{
            "chunk_id": "counter-status",
            "content": "客户携带订单号至售后服务台办理退款后，三个工作日到账。",
        }]

        self.assertEqual(filter_evidence_for_question("如何申请退款", status), [])
        self.assertEqual(filter_evidence_for_question("退款多久到账", status), status)

    def test_counter_visit_followed_by_arrival_time_is_not_a_procedure(self):
        status = [{
            "chunk_id": "counter-arrival",
            "content": "携带订单号至服务台办理退款后，款项三个工作日到账。",
        }]

        self.assertEqual(filter_evidence_for_question("如何申请退款", status), [])

    def test_procedure_chunk_can_answer_how_to_question(self):
        procedure = [{
            "chunk_id": "steps",
            "content": "打开订单详情页，点击申请退款，填写原因后提交。",
        }]

        self.assertEqual(
            filter_evidence_for_question("我应该怎么退款", procedure), procedure
        )

    def test_does_not_modify_original_evidence(self):
        timing = {"chunk_id": "timing", "content": "退款审核通过后三个工作日到账。"}
        procedure = {"chunk_id": "steps", "content": "进入订单详情页并提交退款申请。"}
        evidence = [timing, procedure]

        self.assertEqual(
            filter_evidence_for_question("如何申请退款", evidence), [procedure]
        )
        self.assertEqual(evidence, [timing, procedure])

    def test_topic_mismatch_is_rejected_even_when_both_are_procedures(self):
        refund_steps = [{"chunk_id": "refund", "content": "打开订单详情页，点击申请退款。"}]

        self.assertEqual(filter_evidence_for_question("订单怎么取消", refund_steps), [])

    def test_timing_question_requires_a_time_expression(self):
        status_only = [{"chunk_id": "refund", "content": "退款申请已经审核通过。"}]

        self.assertEqual(filter_evidence_for_question("退款多久到账", status_only), [])

    def test_exact_error_code_must_match(self):
        wrong_code = [{"chunk_id": "error", "content": "PAYMENT_500 表示支付服务暂时不可用。"}]

        self.assertEqual(filter_evidence_for_question("PAYMENT_403 表示什么", wrong_code), [])

    def test_destination_question_requires_destination_evidence(self):
        eligibility = [{"chunk_id": "invoice", "content": "订单完成后可以申请电子发票。"}]

        self.assertEqual(filter_evidence_for_question("电子发票会发送到哪里", eligibility), [])

    def test_explicit_business_qualifier_must_match(self):
        standard = [{"chunk_id": "standard", "content": "普通退款审核通过后三个工作日到账。"}]
        normal_ticket = [{"chunk_id": "normal", "content": "普通优先级工单一个工作日内响应。"}]

        self.assertEqual(filter_evidence_for_question("紧急退款多久能到账", standard), [])
        self.assertEqual(filter_evidence_for_question("高优先级工单多久响应", normal_ticket), [])


if __name__ == "__main__":
    unittest.main()

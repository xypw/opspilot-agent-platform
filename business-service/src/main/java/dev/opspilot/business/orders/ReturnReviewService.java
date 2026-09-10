package dev.opspilot.business.orders;

import com.fasterxml.jackson.annotation.JsonProperty;
import org.springframework.stereotype.Service;

/** 演示商家规则：申请前只读预审，不创建申请、不退款。 */
@Service
public class ReturnReviewService {
    public enum ReasonCode { PERSONAL_PREFERENCE, QUALITY_ISSUE, OTHER }
    public enum ReviewDecision { ACCEPTABLE, REJECTED, MANUAL_REVIEW }
    public record ReviewRequest(
            @JsonProperty("return_reason") String returnReason,
            @JsonProperty("reason_code") ReasonCode reasonCode) {}
    public record ReviewResponse(
            @JsonProperty("order_id") String orderId,
            ReviewDecision decision,
            String reason) {}

    private final ReturnEligibilityService eligibility;

    public ReturnReviewService(ReturnEligibilityService eligibility) {
        this.eligibility = eligibility;
    }

    public ReviewResponse review(OrderResponse order, ReviewRequest request) {
        return reviewWithEligibility(order, request, eligibility.evaluate(order));
    }

    // 仅同包草稿服务可传入服务器保存的资格；HTTP 不能提供此参数。
    ReviewResponse reviewWithEligibility(OrderResponse order, ReviewRequest request,
                                          ReturnEligibilityResponse current) {
        // 原因来自外部输入；存在文字不代表已核实。
        if (request == null || request.reasonCode() == null || request.returnReason() == null
                || request.returnReason().isBlank() || request.returnReason().length() > 1000) {
            throw new IllegalArgumentException("INVALID_RETURN_REASON");
        }
        // 恢复可能跨日：重新使用当前订单和服务器日期，拒绝客户端传来的旧资格。
        if (current.decision() == ReturnDecision.NOT_ALLOWED) {
            return new ReviewResponse(order.id(), ReviewDecision.REJECTED, current.reason());
        }
        if (current.decision() == ReturnDecision.NO_REASON_ALLOWED) {
            return new ReviewResponse(order.id(), ReviewDecision.ACCEPTABLE, "NO_REASON_WINDOW");
        }
        if (request.reasonCode() == ReasonCode.PERSONAL_PREFERENCE) {
            return new ReviewResponse(order.id(), ReviewDecision.REJECTED, "PERSONAL_REASON_OUTSIDE_WINDOW");
        }
        // 质量问题需要材料核实；OTHER 无明确依据，也进入人工审核。
        return new ReviewResponse(order.id(), ReviewDecision.MANUAL_REVIEW, "EVIDENCE_REVIEW_REQUIRED");
    }
}

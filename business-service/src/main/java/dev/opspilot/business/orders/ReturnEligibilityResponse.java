package dev.opspilot.business.orders;

import com.fasterxml.jackson.annotation.JsonProperty;

/** Java 计算后的确定性退货规则结果，供 Python Agent 查询和解释。 */
public record ReturnEligibilityResponse(
        @JsonProperty("order_id") String orderId,
        ReturnDecision decision,
        @JsonProperty("can_apply") boolean canApply,
        @JsonProperty("reason_required") boolean reasonRequired,
        @JsonProperty("days_since_delivery") Long daysSinceDelivery,
        String reason) {
}

package dev.opspilot.business.orders;

import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonIgnore;

/** Java 计算后的确定性退货规则结果，供 Python Agent 查询和解释。 */
public record ReturnEligibilityResponse(
        @JsonProperty("order_id") String orderId,
        ReturnDecision decision,
        @JsonProperty("can_apply") boolean canApply,
        @JsonProperty("reason_required") boolean reasonRequired,
        @JsonProperty("days_since_delivery") Long daysSinceDelivery,
        String reason,
        @JsonIgnore String policyFingerprint) {
    public ReturnEligibilityResponse(String orderId, ReturnDecision decision, boolean canApply,
            boolean reasonRequired, Long daysSinceDelivery, String reason) {
        this(orderId, decision, canApply, reasonRequired, daysSinceDelivery, reason, null);
    }
}

package dev.opspilot.business.orders;

import java.time.Clock;
import java.time.LocalDate;
import java.time.temporal.ChronoUnit;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import static dev.opspilot.business.orders.OrderReturnPolicy.*;

/** 在可信的 Java 业务层执行退货规则，不让大模型自行计算日期或决定资格。 */
@Service
public class ReturnEligibilityService {
    static final int NO_REASON_WINDOW_DAYS = 7;
    private final Clock clock;
    private final ReturnPolicyRepository policies;

    public ReturnEligibilityService(Clock clock) {
        this(clock, orderId -> OrderReturnPolicy.unknown());
    }

    @Autowired
    public ReturnEligibilityService(Clock clock, ReturnPolicyRepository policies) {
        this.clock = clock;
        this.policies = policies;
    }

    public ReturnEligibilityResponse evaluate(OrderResponse order) {
        if ("cancelled".equals(order.status())) {
            return notAllowed(order.id(), null, "ORDER_CANCELLED");
        }
        if (!"delivered".equals(order.status()) || order.deliveredAt() == null) {
            return notAllowed(order.id(), null, "ORDER_NOT_DELIVERED");
        }

        long days = ChronoUnit.DAYS.between(order.deliveredAt(), LocalDate.now(clock));
        if (days < 0) {
            throw new IllegalStateException("delivered_at cannot be in the future");
        }
        var policy = policies.find(order.id());
        if (policy == null) policy = OrderReturnPolicy.unknown();
        if (policy.category() == null || policy.category() == Category.UNKNOWN
                || policy.version() == null || policy.version().isBlank()
                || policy.merchantNoReasonDays() == null || policy.merchantNoReasonDays() < 0
                || policy.merchantNoReasonDays() > 365) {
            return needsReview(order.id(), days, "POLICY_FACTS_REQUIRED", policy);
        }
        if (policy.condition() == null || policy.condition() == Condition.UNKNOWN) {
            return needsReview(order.id(), days, "PRODUCT_CONDITION_UNKNOWN", policy);
        }
        if (policy.condition() == Condition.NOT_INTACT) {
            return needsReview(order.id(), days, "PRODUCT_NOT_INTACT", policy);
        }
        boolean excluded = switch (policy.category()) {
            case CUSTOM_MADE, FRESH_PERISHABLE, NEWSPAPER_PERIODICAL, DISCLOSED_DISCOUNT -> true;
            case DIGITAL, SEALED_HEALTH -> Boolean.TRUE.equals(policy.downloadedOrUnsealed());
            case VALUE_SENSITIVE -> Boolean.TRUE.equals(policy.activated());
            default -> false;
        };
        if (((policy.category() == Category.DIGITAL || policy.category() == Category.SEALED_HEALTH)
                && policy.downloadedOrUnsealed() == null)
                || (policy.category() == Category.VALUE_SENSITIVE && policy.activated() == null)) {
            return needsReview(order.id(), days, "POLICY_FACTS_REQUIRED", policy);
        }
        // 记录不完整时不得仅凭“不支持退货”标签剥夺权利，交人工核实标注及购买确认。
        boolean withinMerchantPromise = policy.merchantNoReasonDays() > 0
                && days <= policy.merchantNoReasonDays();
        if (excluded && !Boolean.TRUE.equals(policy.exclusionConfirmedAtPurchase()) && !withinMerchantPromise) {
            return needsReview(order.id(), days, "EXCLUSION_CONFIRMATION_REQUIRED", policy);
        }
        int window = Math.max(excluded ? 0 : NO_REASON_WINDOW_DAYS, policy.merchantNoReasonDays());
        if (window > 0 && days <= window) {
            return new ReturnEligibilityResponse(
                    order.id(), ReturnDecision.NO_REASON_ALLOWED, true, false, days,
                    window == 7 ? "WITHIN_7_DAY_NO_REASON_WINDOW" : "WITHIN_MERCHANT_NO_REASON_WINDOW",
                    policy.fingerprint());
        }
        // 不适用无理由不等于没有质量售后权利；不再设置所有商品统一的 15 天拒绝线。
        return needsReview(order.id(), days,
                excluded && window == 0 ? "NO_REASON_CATEGORY_EXCLUDED" : "NO_REASON_WINDOW_EXPIRED", policy);
    }

    private ReturnEligibilityResponse needsReview(String orderId, long days, String reason,
                                                   OrderReturnPolicy policy) {
        return new ReturnEligibilityResponse(orderId, ReturnDecision.REASON_REQUIRED,
                true, true, days, reason, policy.fingerprint());
    }

    private ReturnEligibilityResponse notAllowed(String orderId, Long days, String reason) {
        return new ReturnEligibilityResponse(
                orderId, ReturnDecision.NOT_ALLOWED, false, false, days, reason);
    }
}

package dev.opspilot.business.orders;

import java.time.Clock;
import java.time.LocalDate;
import java.time.temporal.ChronoUnit;
import org.springframework.stereotype.Service;

/** 在可信的 Java 业务层执行退货规则，不让大模型自行计算日期或决定资格。 */
@Service
public class ReturnEligibilityService {
    static final int NO_REASON_WINDOW_DAYS = 7;
    static final int RETURN_DEADLINE_DAYS = 15;

    private final Clock clock;

    public ReturnEligibilityService(Clock clock) {
        this.clock = clock;
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
        if (days <= NO_REASON_WINDOW_DAYS) {
            return new ReturnEligibilityResponse(
                    order.id(), ReturnDecision.NO_REASON_ALLOWED, true, false, days,
                    "WITHIN_7_DAY_NO_REASON_WINDOW");
        }
        if (days <= RETURN_DEADLINE_DAYS) {
            return new ReturnEligibilityResponse(
                    order.id(), ReturnDecision.REASON_REQUIRED, true, true, days,
                    "WITHIN_15_DAY_CONDITIONAL_WINDOW");
        }
        return notAllowed(order.id(), days, "RETURN_WINDOW_EXPIRED");
    }

    private ReturnEligibilityResponse notAllowed(String orderId, Long days, String reason) {
        return new ReturnEligibilityResponse(
                orderId, ReturnDecision.NOT_ALLOWED, false, false, days, reason);
    }
}

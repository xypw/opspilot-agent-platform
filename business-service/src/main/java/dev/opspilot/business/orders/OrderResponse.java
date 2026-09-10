package dev.opspilot.business.orders;

import com.fasterxml.jackson.annotation.JsonProperty;
import java.time.LocalDate;

/** Java/Python 共享的订单查询契约；金额使用整数分，避免浮点精度误差。 */
public record OrderResponse(
        String id,
        String status,
        String product,
        @JsonProperty("delivered_at") LocalDate deliveredAt,
        @JsonProperty("amount_cents") int amountCents) {
}

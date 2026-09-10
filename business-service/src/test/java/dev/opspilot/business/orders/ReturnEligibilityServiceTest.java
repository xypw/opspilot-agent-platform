package dev.opspilot.business.orders;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.time.Clock;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneId;
import org.junit.jupiter.api.Test;

class ReturnEligibilityServiceTest {
    private static final ZoneId ZONE = ZoneId.of("Asia/Shanghai");
    private static final Clock CLOCK = Clock.fixed(Instant.parse("2026-09-07T04:00:00Z"), ZONE);
    private final ReturnEligibilityService service = new ReturnEligibilityService(CLOCK);

    @Test
    void day7StillAllowsNoReasonReturn() {
        var result = service.evaluate(deliveredOn("2026-08-31"));

        assertThat(result.decision()).isEqualTo(ReturnDecision.NO_REASON_ALLOWED);
        assertThat(result.canApply()).isTrue();
        assertThat(result.reasonRequired()).isFalse();
        assertThat(result.daysSinceDelivery()).isEqualTo(7);
    }

    @Test
    void day8RequiresAReason() {
        var result = service.evaluate(deliveredOn("2026-08-30"));

        assertThat(result.decision()).isEqualTo(ReturnDecision.REASON_REQUIRED);
        assertThat(result.canApply()).isTrue();
        assertThat(result.reasonRequired()).isTrue();
        assertThat(result.daysSinceDelivery()).isEqualTo(8);
    }

    @Test
    void day15StillAcceptsAConditionalApplication() {
        var result = service.evaluate(deliveredOn("2026-08-23"));

        assertThat(result.decision()).isEqualTo(ReturnDecision.REASON_REQUIRED);
        assertThat(result.canApply()).isTrue();
        assertThat(result.daysSinceDelivery()).isEqualTo(15);
    }

    @Test
    void day16RejectsReturn() {
        var result = service.evaluate(deliveredOn("2026-08-22"));

        assertThat(result.decision()).isEqualTo(ReturnDecision.NOT_ALLOWED);
        assertThat(result.canApply()).isFalse();
        assertThat(result.reason()).isEqualTo("RETURN_WINDOW_EXPIRED");
        assertThat(result.daysSinceDelivery()).isEqualTo(16);
    }

    @Test
    void undeliveredAndCancelledOrdersCannotApply() {
        var processing = new OrderResponse("O-2002", "processing", "无线鼠标", null, 12900);
        var cancelled = new OrderResponse("O-2003", "cancelled", "显示器", null, 159900);

        assertThat(service.evaluate(processing).reason()).isEqualTo("ORDER_NOT_DELIVERED");
        assertThat(service.evaluate(cancelled).reason()).isEqualTo("ORDER_CANCELLED");
        assertThat(service.evaluate(processing).canApply()).isFalse();
        assertThat(service.evaluate(cancelled).canApply()).isFalse();
    }

    @Test
    void rejectsImpossibleFutureDeliveryDate() {
        assertThatThrownBy(() -> service.evaluate(deliveredOn("2026-09-08")))
                .isInstanceOf(IllegalStateException.class)
                .hasMessage("delivered_at cannot be in the future");
    }

    private OrderResponse deliveredOn(String date) {
        return new OrderResponse(
                "O-TEST", "delivered", "测试商品", LocalDate.parse(date), 3555);
    }
}

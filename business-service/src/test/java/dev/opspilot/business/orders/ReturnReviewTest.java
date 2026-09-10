package dev.opspilot.business.orders;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import java.time.*;
import org.junit.jupiter.api.Test;
import static dev.opspilot.business.orders.ReturnReviewService.*;

class ReturnReviewTest {
    private final ReturnReviewService service = new ReturnReviewService(new ReturnEligibilityService(
            Clock.fixed(Instant.parse("2026-09-07T04:00:00Z"), ZoneId.of("Asia/Shanghai"))));

    private OrderResponse delivered(int days) {
        return new OrderResponse("O-2001", "delivered", "键盘",
                LocalDate.of(2026, 9, 7).minusDays(days), 3555);
    }

    @Test
    void boundariesAndReasons() {
        var preference = new ReviewRequest("不喜欢", ReasonCode.PERSONAL_PREFERENCE);
        var quality = new ReviewRequest("按键失灵", ReasonCode.QUALITY_ISSUE);
        assertThat(service.review(delivered(7), preference).decision()).isEqualTo(ReviewDecision.ACCEPTABLE);
        assertThat(service.review(delivered(8), preference).decision()).isEqualTo(ReviewDecision.REJECTED);
        assertThat(service.review(delivered(15), quality).decision()).isEqualTo(ReviewDecision.MANUAL_REVIEW);
        assertThat(service.review(delivered(10), new ReviewRequest("其他", ReasonCode.OTHER)).decision())
                .isEqualTo(ReviewDecision.MANUAL_REVIEW);
        assertThat(service.review(delivered(16), quality).reason()).isEqualTo("RETURN_WINDOW_EXPIRED");
    }

    @Test
    void cancelledOrderAndEmptyReasonCannotPass() {
        assertThat(service.review(new OrderResponse("O-2001", "cancelled", "键盘", null, 3555),
                new ReviewRequest("损坏", ReasonCode.QUALITY_ISSUE)).reason()).isEqualTo("ORDER_CANCELLED");
        assertThatThrownBy(() -> service.review(delivered(10), new ReviewRequest(" ", ReasonCode.OTHER)))
                .isInstanceOf(IllegalArgumentException.class);
    }
}

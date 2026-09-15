package dev.opspilot.business.orders;

import static org.assertj.core.api.Assertions.*;
import java.time.*;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.Test;
import static dev.opspilot.business.orders.ReturnReviewService.*;

class ReturnDraftTest {
    static class TestClock extends Clock {
        Instant now = Instant.parse("2026-09-07T15:30:00Z"); // 上海第15天23:30
        public ZoneId getZone() { return ZoneId.of("Asia/Shanghai"); }
        public Clock withZone(ZoneId zone) { return Clock.fixed(now, zone); }
        public Instant instant() { return now; }
    }
    private final TestClock clock = new TestClock();
    private final ReturnEligibilityService eligibility = new ReturnEligibilityService(clock);
    private final ReturnDraftService drafts = new ReturnDraftService(clock, eligibility,
            new ReturnReviewService(eligibility), new InMemoryReturnDraftStore());
    private final OrderResponse order = new OrderResponse("O-2001", "delivered", "键盘",
            LocalDate.of(2026, 8, 23), 3555);
    private final ReviewRequest reason = new ReviewRequest("按键失灵", ReasonCode.QUALITY_ISSUE);

    @Test
    void crossesDay16WithinHourAndRetryReturnsSameResult() {
        var draft = drafts.start(order);
        clock.now = clock.now.plusSeconds(3599);
        assertThat(eligibility.evaluate(order).canApply()).isFalse();
        var review = drafts.submit(order, draft.draftId(), reason);
        assertThat(review.decision()).isEqualTo(ReviewDecision.MANUAL_REVIEW);
        clock.now = clock.now.plusSeconds(100);
        assertThat(drafts.submit(order, draft.draftId(), reason)).isEqualTo(review);
        assertThat(drafts.get(order.id(), draft.draftId()).status()).isEqualTo("REVIEWED");
    }

    @Test
    void exactHourExpiresAndRepeatedCreationCannotExtendIt() {
        var draft = drafts.start(order);
        clock.now = clock.now.plusSeconds(1800);
        assertThat(drafts.start(order)).isEqualTo(draft);
        clock.now = clock.now.plusSeconds(1800);
        assertThat(drafts.get(order.id(), draft.draftId()).status()).isEqualTo("EXPIRED");
        assertThat(drafts.start(order).expiresAt()).isEqualTo(draft.expiresAt());
        assertThatThrownBy(() -> drafts.submit(order, draft.draftId(), reason))
                .isInstanceOf(ReturnDraftService.DraftError.class).hasMessage("DRAFT_EXPIRED");
    }

    @Test
    void cannotStartAfterDay15OrBypassCancellation() {
        clock.now = clock.now.plusSeconds(3600);
        assertThatThrownBy(() -> drafts.start(order)).hasMessage("ORDER_NOT_ELIGIBLE");
        clock.now = clock.now.minusSeconds(3600);
        var draft = drafts.start(order);
        var cancelled = new OrderResponse(order.id(), "cancelled", order.product(), order.deliveredAt(), 3555);
        assertThatThrownBy(() -> drafts.submit(cancelled, draft.draftId(), reason)).hasMessage("ORDER_CHANGED");
        assertThatThrownBy(() -> drafts.submit(order, "unknown", reason)).hasMessage("DRAFT_NOT_FOUND");
    }

    @Test
    void blankReasonDoesNotCompleteDraft() {
        var draft = drafts.start(order);
        assertThatThrownBy(() -> drafts.submit(order, draft.draftId(),
                new ReviewRequest(" ", ReasonCode.OTHER))).isInstanceOf(IllegalArgumentException.class);
        assertThat(drafts.get(order.id(), draft.draftId()).status()).isEqualTo("WAITING_REASON");
    }

    @Test
    void confirmationCreatesOneApplicationAndCancellationBlocksCreation() {
        // 使用第7天订单，七天无理由无需先提交原因。
        var noReasonOrder = new OrderResponse(
                "O-2002", "delivered", "鼠标", LocalDate.of(2026, 8, 31), 3555);
        var draft = drafts.start(noReasonOrder);
        var first = drafts.confirm(noReasonOrder, draft.draftId());
        var retry = drafts.confirm(noReasonOrder, draft.draftId());
        assertThat(first).isEqualTo(retry);
        assertThat(first.status()).isEqualTo("SUBMITTED");
        assertThat(first.refundAmountCents()).isEqualTo(3555);
        assertThat(drafts.get(noReasonOrder.id(), draft.draftId()).status()).isEqualTo("SUBMITTED");

        var secondOrder = new OrderResponse(
                "O-2003", "delivered", "显示器", LocalDate.of(2026, 8, 31), 159900);
        var cancelledDraft = drafts.start(secondOrder);
        assertThat(drafts.cancel(secondOrder.id(), cancelledDraft.draftId()).status()).isEqualTo("CANCELLED");
        assertThatThrownBy(() -> drafts.confirm(secondOrder, cancelledDraft.draftId()))
                .hasMessage("DRAFT_CANCELLED");
    }

    @Test
    void concurrentConfirmationReturnsTheSameApplication() throws Exception {
        var noReasonOrder = new OrderResponse(
                "O-2002", "delivered", "鼠标", LocalDate.of(2026, 8, 31), 3555);
        var draft = drafts.start(noReasonOrder);
        var ready = new CountDownLatch(2);
        var start = new CountDownLatch(1);
        var pool = Executors.newFixedThreadPool(2);
        try {
            var task = (java.util.concurrent.Callable<ReturnDraftService.ApplicationResponse>) () -> {
                ready.countDown();
                if (!start.await(5, TimeUnit.SECONDS)) throw new IllegalStateException("START_TIMEOUT");
                return drafts.confirm(noReasonOrder, draft.draftId());
            };
            var first = pool.submit(task);
            var second = pool.submit(task);
            assertThat(ready.await(5, TimeUnit.SECONDS)).isTrue();
            start.countDown();
            assertThat(first.get(5, TimeUnit.SECONDS)).isEqualTo(second.get(5, TimeUnit.SECONDS));
            assertThat(drafts.get(noReasonOrder.id(), draft.draftId()).status()).isEqualTo("SUBMITTED");
        } finally {
            start.countDown();
            pool.shutdownNow();
        }
    }

    @Test
    void manualReviewCannotBeConfirmedAsAccepted() {
        var draft = drafts.start(order);
        drafts.submit(order, draft.draftId(), reason);
        assertThatThrownBy(() -> drafts.confirm(order, draft.draftId()))
                .hasMessage("RETURN_NOT_ACCEPTABLE");
    }

    @Test
    void changedAmountOrProductCannotUseOldConfirmation() {
        var original = new OrderResponse("O-2002", "delivered", "键盘",
                LocalDate.of(2026, 8, 31), 39900);
        var draft = drafts.start(original);
        var changedAmount = new OrderResponse(original.id(), original.status(), original.product(),
                original.deliveredAt(), 49900);
        var changedProduct = new OrderResponse(original.id(), original.status(), "另一款键盘",
                original.deliveredAt(), original.amountCents());
        for (var changed : List.of(changedAmount, changedProduct)) {
            assertThatThrownBy(() -> drafts.confirm(changed, draft.draftId()))
                    .isInstanceOf(ReturnDraftService.DraftError.class).hasMessage("ORDER_CHANGED");
            assertThat(drafts.get(original.id(), draft.draftId()).status()).isNotEqualTo("SUBMITTED");
        }
    }

    @Test
    void completedApplicationRetryReturnsOriginalEvenIfOrderChanges() {
        var original = new OrderResponse("O-2002", "delivered", "键盘",
                LocalDate.of(2026, 8, 31), 39900);
        var draft = drafts.start(original);
        var application = drafts.confirm(original, draft.draftId());
        var changed = new OrderResponse(original.id(), original.status(), original.product(),
                original.deliveredAt(), 49900);
        assertThat(drafts.confirm(changed, draft.draftId())).isEqualTo(application);
        assertThat(application.refundAmountCents()).isEqualTo(39900);
    }

    @Test
    void staleDraftRefreshCreatesNewSnapshotAndInvalidatesOldId() {
        var original = new OrderResponse("O-2002", "delivered", "键盘",
                LocalDate.of(2026, 8, 31), 39900);
        var oldDraft = drafts.start(original);
        var latest = new OrderResponse(original.id(), original.status(), original.product(),
                original.deliveredAt(), 49900);

        assertThatThrownBy(() -> drafts.confirm(latest, oldDraft.draftId()))
                .hasMessage("ORDER_CHANGED");
        var newDraft = drafts.refresh(latest, oldDraft.draftId());

        assertThat(newDraft.draftId()).isNotEqualTo(oldDraft.draftId());
        assertThat(newDraft.amountCents()).isEqualTo(49900);
        assertThatThrownBy(() -> drafts.confirm(latest, oldDraft.draftId()))
                .hasMessage("DRAFT_NOT_FOUND");
        assertThat(drafts.confirm(latest, newDraft.draftId()).refundAmountCents()).isEqualTo(49900);
    }

    @Test
    void unchangedDraftCannotResetItsDeadlineThroughRefresh() {
        var original = new OrderResponse("O-2002", "delivered", "键盘",
                LocalDate.of(2026, 8, 31), 39900);
        var draft = drafts.start(original);
        clock.now = clock.now.plusSeconds(1800);

        assertThatThrownBy(() -> drafts.refresh(original, draft.draftId()))
                .hasMessage("DRAFT_NOT_STALE");
        assertThat(drafts.get(original.id(), draft.draftId()).expiresAt()).isEqualTo(draft.expiresAt());
    }
}

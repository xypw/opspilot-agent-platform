package dev.opspilot.business.orders;

import static org.assertj.core.api.Assertions.*;
import static dev.opspilot.business.orders.OrderReturnPolicy.*;
import static dev.opspilot.business.orders.ReturnReviewService.*;
import java.time.*;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.EnumSource;

class CategoryReturnPolicyTest {
    private final Clock clock = Clock.fixed(Instant.parse("2026-10-21T04:00:00Z"), ZoneId.of("Asia/Shanghai"));
    private OrderResponse order(int days) {
        return new OrderResponse("O-8001", "delivered", "测试商品", LocalDate.now(clock).minusDays(days), 1000);
    }
    private OrderReturnPolicy policy(Category c, Condition condition, Boolean confirmed,
            Boolean opened, Boolean activated, int days) {
        return new OrderReturnPolicy(c, condition, confirmed, opened, activated, days, "fixture-v1");
    }
    private ReturnEligibilityService service(OrderReturnPolicy policy) {
        return new ReturnEligibilityService(clock, id -> policy);
    }
    private ReviewRequest preference() { return new ReviewRequest("不喜欢", ReasonCode.PERSONAL_PREFERENCE); }

    @ParameterizedTest
    @EnumSource(value = Category.class, names = {"FRESH_PERISHABLE", "CUSTOM_MADE", "NEWSPAPER_PERIODICAL"})
    void excludedCategoryDoesNotAutomaticallyQualifyEvenOnFirstDay(Category category) {
        var eligibility = service(policy(category, Condition.INTACT, true, false, false, 0));
        assertThat(eligibility.evaluate(order(1)).decision()).isEqualTo(ReturnDecision.REASON_REQUIRED);
        var review = new ReturnReviewService(eligibility);
        assertThat(review.review(order(1), preference()).decision()).isEqualTo(ReviewDecision.REJECTED);
        assertThat(review.review(order(30), new ReviewRequest("腐败或故障", ReasonCode.QUALITY_ISSUE)).decision())
                .isEqualTo(ReviewDecision.MANUAL_REVIEW);
    }
    @ParameterizedTest
    @EnumSource(value = Category.class, names = {"FRESH_PERISHABLE", "CUSTOM_MADE", "DIGITAL", "SEALED_HEALTH", "VALUE_SENSITIVE", "DISCLOSED_DISCOUNT"})
    void missingPurchaseConfirmationCannotBecomeAnAutomaticRejection(Category category) {
        var eligibility = service(policy(category, Condition.INTACT, null, true, true, 0));
        assertThat(eligibility.evaluate(order(1)).reason()).isEqualTo("EXCLUSION_CONFIRMATION_REQUIRED");
        assertThat(new ReturnReviewService(eligibility).review(order(1), preference()).decision())
                .isEqualTo(ReviewDecision.MANUAL_REVIEW);
    }
    @ParameterizedTest
    @EnumSource(value = Category.class, names = {"DIGITAL", "SEALED_HEALTH"})
    void unopenedAndOpenedGoodsHaveDifferentResults(Category category) {
        assertThat(service(policy(category, Condition.INTACT, true, false, false, 0))
                .evaluate(order(1)).decision()).isEqualTo(ReturnDecision.NO_REASON_ALLOWED);
        assertThat(service(policy(category, Condition.INTACT, true, true, false, 0))
                .evaluate(order(1)).reason()).isEqualTo("NO_REASON_CATEGORY_EXCLUDED");
    }
    @Test
    void activationExceptionRequiresActivationAndPurchaseConfirmation() {
        assertThat(service(policy(Category.VALUE_SENSITIVE, Condition.INTACT, true, false, true, 0))
                .evaluate(order(1)).reason()).isEqualTo("NO_REASON_CATEGORY_EXCLUDED");
        assertThat(service(policy(Category.VALUE_SENSITIVE, Condition.INTACT, true, false, false, 0))
                .evaluate(order(1)).decision()).isEqualTo(ReturnDecision.NO_REASON_ALLOWED);
    }
    @Test
    void reasonableInspectionIsNotAutomaticallyTreatedAsDamage() {
        assertThat(service(policy(Category.STANDARD, Condition.REASONABLE_INSPECTION, false, true, false, 0))
                .evaluate(order(7)).decision()).isEqualTo(ReturnDecision.NO_REASON_ALLOWED);
        assertThat(service(policy(Category.STANDARD, Condition.NOT_INTACT, false, true, false, 0))
                .evaluate(order(1)).decision()).isEqualTo(ReturnDecision.REASON_REQUIRED);
    }
    @Test
    void merchantPromiseCanExtendButCannotShortenOrdinarySevenDayWindow() {
        assertThat(service(policy(Category.STANDARD, Condition.INTACT, false, false, false, 30))
                .evaluate(order(20)).decision()).isEqualTo(ReturnDecision.NO_REASON_ALLOWED);
        assertThat(service(policy(Category.STANDARD, Condition.INTACT, false, false, false, 1))
                .evaluate(order(7)).decision()).isEqualTo(ReturnDecision.NO_REASON_ALLOWED);
    }
    @Test
    void unknownFactsRequireManualReviewRatherThanDefaultApprovalOrDenial() {
        var unknown = new ReturnEligibilityService(clock);
        assertThat(unknown.evaluate(order(1)).reason()).isEqualTo("POLICY_FACTS_REQUIRED");
        assertThat(new ReturnReviewService(unknown).review(order(1), preference()).decision())
                .isEqualTo(ReviewDecision.MANUAL_REVIEW);
    }
    @Test
    void favorablePromiseDoesNotRequireInvokingACategoryExclusion() {
        assertThat(service(policy(Category.FRESH_PERISHABLE, Condition.INTACT, null, false, false, 7))
                .evaluate(order(1)).decision()).isEqualTo(ReturnDecision.NO_REASON_ALLOWED);
    }
    @Test
    void qualityClaimInsideSevenDaysCannotBypassManualReviewByConfirmation() {
        var eligibility = service(OrderReturnPolicy.standard());
        var drafts = new ReturnDraftService(clock, eligibility, new ReturnReviewService(eligibility), new InMemoryReturnDraftStore());
        var order = order(1);
        var draft = drafts.start(order);
        assertThat(drafts.submit(order, draft.draftId(), new ReviewRequest("质量问题", ReasonCode.QUALITY_ISSUE)).decision())
                .isEqualTo(ReviewDecision.MANUAL_REVIEW);
        assertThatThrownBy(() -> drafts.confirm(order, draft.draftId())).hasMessage("RETURN_NOT_ACCEPTABLE");
    }
    @Test
    void changingPolicyInvalidatesOldApprovalAndRequiresANewDraft() {
        var facts = new AtomicReference<>(OrderReturnPolicy.standard());
        var eligibility = new ReturnEligibilityService(clock, id -> facts.get());
        var drafts = new ReturnDraftService(clock, eligibility, new ReturnReviewService(eligibility), new InMemoryReturnDraftStore());
        var order = order(1);
        var draft = drafts.start(order);
        facts.set(policy(Category.CUSTOM_MADE, Condition.INTACT, true, false, false, 0));
        assertThatThrownBy(() -> drafts.confirm(order, draft.draftId())).hasMessage("ORDER_CHANGED");
        var refreshed = drafts.refresh(order, draft.draftId());
        assertThat(refreshed.draftId()).isNotEqualTo(draft.draftId());
        assertThatThrownBy(() -> drafts.confirm(order, refreshed.draftId())).hasMessage("RETURN_NOT_ACCEPTABLE");
    }
}

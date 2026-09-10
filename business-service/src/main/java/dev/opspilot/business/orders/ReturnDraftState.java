package dev.opspilot.business.orders;

import java.time.Instant;
import static dev.opspilot.business.orders.ReturnDraftService.ApplicationResponse;
import static dev.opspilot.business.orders.ReturnReviewService.*;

/** 仅在业务服务与存储实现之间传递的退货流程聚合状态。 */
final class ReturnDraftState {
    String id;
    OrderResponse order;
    ReturnEligibilityResponse eligibility;
    Instant started;
    Instant expires;
    ReviewRequest submitted;
    ReviewResponse result;
    ApplicationResponse application;
    boolean cancelled;
}

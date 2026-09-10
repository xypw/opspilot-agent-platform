package dev.opspilot.business.orders;

import com.fasterxml.jackson.annotation.JsonProperty;
import java.time.*;
import java.time.temporal.ChronoUnit;
import java.util.*;
import org.springframework.stereotype.Service;
import static dev.opspilot.business.orders.ReturnReviewService.*;

/** 退货业务规则；状态保存委托给 ReturnDraftStore。 */
@Service
public class ReturnDraftService {
    public record DraftResponse(
            @JsonProperty("draft_id") String draftId,
            @JsonProperty("order_id") String orderId,
            String product,
            @JsonProperty("amount_cents") int amountCents,
            @JsonProperty("started_at") Instant startedAt,
            @JsonProperty("expires_at") Instant expiresAt,
            String status) {}
    public record ApplicationResponse(
            @JsonProperty("application_id") String applicationId,
            @JsonProperty("order_id") String orderId,
            String product,
            @JsonProperty("refund_amount_cents") int refundAmountCents,
            String status,
            @JsonProperty("created_at") Instant createdAt) {}
    public static class DraftError extends RuntimeException {
        final int status;
        DraftError(int status, String code) { super(code); this.status = status; }
    }
    private final Clock clock;
    private final ReturnEligibilityService eligibility;
    private final ReturnReviewService reviewer;
    private final ReturnDraftStore store;

    public ReturnDraftService(Clock clock, ReturnEligibilityService eligibility,
                              ReturnReviewService reviewer, ReturnDraftStore store) {
        this.clock = clock;
        this.eligibility = eligibility;
        this.reviewer = reviewer;
        this.store = store;
    }

    public DraftResponse start(OrderResponse order) {
        return store.inTransaction(activeStore -> startInStore(activeStore, order));
    }

    private DraftResponse startInStore(ReturnDraftStore activeStore, OrderResponse order) {
        var existing = activeStore.findByOrderId(order.id());
        if (existing.isPresent()) return describe(existing.get());
        var initial = eligibility.evaluate(order);
        if (!initial.canApply()) throw new DraftError(409, "ORDER_NOT_ELIGIBLE");
        if (activeStore.count() >= 10000) throw new DraftError(503, "DRAFT_CAPACITY_REACHED");
        var draft = new ReturnDraftState();
        draft.id = UUID.randomUUID().toString();
        draft.order = order;
        draft.eligibility = initial;
        draft.started = persistentNow();
        draft.expires = draft.started.plus(Duration.ofHours(1));
        // 数据库唯一约束解决两个服务实例同时创建草稿的竞态。
        if (!activeStore.insertIfAbsent(draft)) {
            return describe(activeStore.findByOrderId(order.id())
                    .orElseThrow(() -> new DraftError(503, "DRAFT_WRITE_CONFLICT")));
        }
        return describe(draft);
    }

    public DraftResponse get(String orderId, String draftId) {
        return store.inTransaction(activeStore -> describe(find(activeStore, orderId, draftId)));
    }

    public ReviewResponse submit(OrderResponse order, String draftId, ReviewRequest request) {
        return store.inTransaction(activeStore -> submitInStore(activeStore, order, draftId, request));
    }

    private ReviewResponse submitInStore(ReturnDraftStore activeStore, OrderResponse order,
                                         String draftId, ReviewRequest request) {
        var draft = find(activeStore, order.id(), draftId);
        if (draft.cancelled) throw new DraftError(409, "DRAFT_CANCELLED");
        if (draft.result != null) {
            if (!Objects.equals(draft.submitted, request)) throw new DraftError(409, "DRAFT_ALREADY_SUBMITTED");
            return draft.result; // 已成功接收的相同请求，过期后重试仍返回原结果。
        }
        // 截止时刻不包含在有效期内；Java 即使没有清理任务也能阻止过期提交。
        if (!clock.instant().isBefore(draft.expires)) throw new DraftError(410, "DRAFT_EXPIRED");
        var current = eligibility.evaluate(order);
        // 时间跨日可使用初始资格，但不能绕过订单取消或签收日期被修正。
        if (orderSnapshotChanged(draft, order)
                || (!current.canApply() && !"RETURN_WINDOW_EXPIRED".equals(current.reason()))) {
            throw new DraftError(409, "ORDER_CHANGED");
        }
        var result = reviewer.reviewWithEligibility(order, request, draft.eligibility);
        draft.submitted = request;
        draft.result = result;
        activeStore.save(draft);
        return result;
    }

    public ApplicationResponse confirm(OrderResponse order, String draftId) {
        return store.inTransaction(activeStore -> confirmInStore(activeStore, order, draftId));
    }

    private ApplicationResponse confirmInStore(ReturnDraftStore activeStore,
                                               OrderResponse order, String draftId) {
        var draft = find(activeStore, order.id(), draftId);
        if (draft.application != null) return draft.application;
        if (draft.cancelled) throw new DraftError(409, "DRAFT_CANCELLED");
        ensureActiveAndUnchanged(draft, order);
        boolean acceptable = draft.eligibility.decision() == ReturnDecision.NO_REASON_ALLOWED
                || (draft.result != null && draft.result.decision() == ReviewDecision.ACCEPTABLE);
        if (!acceptable) throw new DraftError(409, "RETURN_NOT_ACCEPTABLE");
        draft.application = new ApplicationResponse(
                UUID.randomUUID().toString(), order.id(), order.product(), order.amountCents(),
                "SUBMITTED", persistentNow());
        activeStore.save(draft);
        return draft.application;
    }

    public DraftResponse cancel(String orderId, String draftId) {
        return store.inTransaction(activeStore -> cancelInStore(activeStore, orderId, draftId));
    }

    private DraftResponse cancelInStore(ReturnDraftStore activeStore, String orderId, String draftId) {
        var draft = find(activeStore, orderId, draftId);
        if (draft.application != null) throw new DraftError(409, "APPLICATION_ALREADY_SUBMITTED");
        draft.cancelled = true;
        activeStore.save(draft);
        return describe(draft);
    }

    public DraftResponse refresh(OrderResponse order, String draftId) {
        return store.inTransaction(activeStore -> refreshInStore(activeStore, order, draftId));
    }

    private DraftResponse refreshInStore(ReturnDraftStore activeStore,
                                         OrderResponse order, String draftId) {
        var oldDraft = find(activeStore, order.id(), draftId);
        if (oldDraft.application != null) throw new DraftError(409, "APPLICATION_ALREADY_SUBMITTED");
        if (oldDraft.cancelled) throw new DraftError(409, "DRAFT_CANCELLED");
        if (!orderSnapshotChanged(oldDraft, order)) throw new DraftError(409, "DRAFT_NOT_STALE");
        var current = eligibility.evaluate(order);
        if (!current.canApply()) throw new DraftError(409, "ORDER_NOT_ELIGIBLE");

        // 新快照必须使用新编号和新确认；替换后旧 draft_id 立即失效。
        var refreshed = new ReturnDraftState();
        refreshed.id = UUID.randomUUID().toString();
        refreshed.order = order;
        refreshed.eligibility = current;
        refreshed.started = persistentNow();
        refreshed.expires = refreshed.started.plus(Duration.ofHours(1));
        if (!activeStore.replace(oldDraft.id, refreshed)) {
            throw new DraftError(409, "DRAFT_WRITE_CONFLICT");
        }
        return describe(refreshed);
    }

    private void ensureActiveAndUnchanged(ReturnDraftState draft, OrderResponse order) {
        if (!clock.instant().isBefore(draft.expires)) throw new DraftError(410, "DRAFT_EXPIRED");
        // 用户确认的是草稿快照；商品或金额变化后，旧确认不能授权新的内容。
        if (orderSnapshotChanged(draft, order)) {
            throw new DraftError(409, "ORDER_CHANGED");
        }
        var current = eligibility.evaluate(order);
        if (!current.canApply() && !"RETURN_WINDOW_EXPIRED".equals(current.reason())) {
            throw new DraftError(409, "ORDER_CHANGED");
        }
    }

    private boolean orderSnapshotChanged(ReturnDraftState draft, OrderResponse order) {
        return order.amountCents() != draft.order.amountCents()
                || !Objects.equals(order.product(), draft.order.product())
                || !Objects.equals(order.status(), draft.order.status())
                || !Objects.equals(order.deliveredAt(), draft.order.deliveredAt());
    }

    private ReturnDraftState find(ReturnDraftStore activeStore, String orderId, String draftId) {
        return activeStore.find(orderId, draftId)
                .orElseThrow(() -> new DraftError(404, "DRAFT_NOT_FOUND"));
    }

    private DraftResponse describe(ReturnDraftState draft) {
        String status = draft.application != null ? "SUBMITTED"
                : draft.cancelled ? "CANCELLED"
                : draft.result != null ? "REVIEWED"
                : clock.instant().isBefore(draft.expires) ? "WAITING_REASON" : "EXPIRED";
        return new DraftResponse(
                draft.id, draft.order.id(), draft.order.product(), draft.order.amountCents(),
                draft.started, draft.expires, status);
    }

    /** PostgreSQL TIMESTAMPTZ 使用微秒精度；写入前统一精度保证重读结果稳定。 */
    private Instant persistentNow() {
        return clock.instant().truncatedTo(ChronoUnit.MICROS);
    }
}

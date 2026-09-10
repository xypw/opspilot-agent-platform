package dev.opspilot.business.orders;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.util.*;
import java.util.function.Function;
import org.flywaydb.core.Flyway;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionTemplate;
import static dev.opspilot.business.orders.ReturnDraftService.ApplicationResponse;
import static dev.opspilot.business.orders.ReturnReviewService.*;

/** PostgreSQL 实现：行锁保护状态迁移，唯一约束兜底幂等性。 */
@Component
@ConditionalOnProperty(name = "opspilot.return-store.backend", havingValue = "postgres")
final class PostgresReturnDraftStore implements ReturnDraftStore {
    private static final String SELECT_STATE = """
            SELECT d.*, a.application_id::text AS application_id,
                   a.refund_amount_cents, a.status AS application_status,
                   a.created_at AS application_created_at
              FROM return_drafts d
              LEFT JOIN return_applications a ON a.draft_id = d.draft_id
            """;

    private final JdbcTemplate jdbc;
    private final TransactionTemplate transactions;

    PostgresReturnDraftStore(JdbcTemplate jdbc, TransactionTemplate transactions, Flyway migratedSchema) {
        this.jdbc = jdbc;
        this.transactions = transactions;
    }

    @Override
    public Optional<ReturnDraftState> findByOrderId(String orderId) {
        return queryOne(SELECT_STATE + " WHERE d.order_id = ? FOR UPDATE OF d", orderId);
    }

    @Override
    public Optional<ReturnDraftState> find(String orderId, String draftId) {
        return queryOne(SELECT_STATE
                + " WHERE d.order_id = ? AND d.draft_id = ?::uuid FOR UPDATE OF d", orderId, draftId);
    }

    @Override
    public long count() {
        Long count = jdbc.queryForObject("SELECT COUNT(*) FROM return_drafts", Long.class);
        return count == null ? 0 : count;
    }

    @Override
    public boolean insertIfAbsent(ReturnDraftState draft) {
        return jdbc.update("""
                INSERT INTO return_drafts (
                    order_id, draft_id, product, amount_cents, order_status, delivered_at,
                    eligibility_decision, eligibility_can_apply, eligibility_reason_required,
                    days_since_delivery, eligibility_reason, started_at, expires_at, cancelled)
                VALUES (?, ?::uuid, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, false)
                ON CONFLICT (order_id) DO NOTHING
                """,
                draft.order.id(), draft.id, draft.order.product(), draft.order.amountCents(),
                draft.order.status(), draft.order.deliveredAt(), draft.eligibility.decision().name(),
                draft.eligibility.canApply(), draft.eligibility.reasonRequired(),
                draft.eligibility.daysSinceDelivery(), draft.eligibility.reason(),
                Timestamp.from(draft.started), Timestamp.from(draft.expires)) == 1;
    }

    @Override
    public boolean replace(String oldDraftId, ReturnDraftState draft) {
        return jdbc.update("""
                UPDATE return_drafts
                   SET draft_id = ?::uuid, product = ?, amount_cents = ?, order_status = ?, delivered_at = ?,
                       eligibility_decision = ?, eligibility_can_apply = ?, eligibility_reason_required = ?,
                       days_since_delivery = ?, eligibility_reason = ?, started_at = ?, expires_at = ?,
                       submitted_reason = NULL, submitted_reason_code = NULL,
                       review_decision = NULL, review_reason = NULL, cancelled = false
                 WHERE order_id = ? AND draft_id = ?::uuid
                """,
                draft.id, draft.order.product(), draft.order.amountCents(), draft.order.status(),
                draft.order.deliveredAt(), draft.eligibility.decision().name(),
                draft.eligibility.canApply(), draft.eligibility.reasonRequired(),
                draft.eligibility.daysSinceDelivery(), draft.eligibility.reason(),
                Timestamp.from(draft.started), Timestamp.from(draft.expires),
                draft.order.id(), oldDraftId) == 1;
    }

    @Override
    public void save(ReturnDraftState draft) {
        int updated = jdbc.update("""
                UPDATE return_drafts
                   SET submitted_reason = ?, submitted_reason_code = ?, review_decision = ?,
                       review_reason = ?, cancelled = ?
                 WHERE order_id = ? AND draft_id = ?::uuid
                """,
                draft.submitted == null ? null : draft.submitted.returnReason(),
                draft.submitted == null ? null : draft.submitted.reasonCode().name(),
                draft.result == null ? null : draft.result.decision().name(),
                draft.result == null ? null : draft.result.reason(),
                draft.cancelled, draft.order.id(), draft.id);
        if (updated != 1) throw new IllegalStateException("RETURN_DRAFT_NOT_SAVED");

        if (draft.application != null) {
            // draft_id 的唯一约束是正式申请的最终幂等兜底。
            jdbc.update("""
                    INSERT INTO return_applications (
                        application_id, draft_id, order_id, product,
                        refund_amount_cents, status, created_at)
                    VALUES (?::uuid, ?::uuid, ?, ?, ?, ?, ?)
                    ON CONFLICT (draft_id) DO NOTHING
                    """,
                    draft.application.applicationId(), draft.id, draft.application.orderId(),
                    draft.application.product(), draft.application.refundAmountCents(),
                    draft.application.status(), Timestamp.from(draft.application.createdAt()));
        }
    }

    @Override
    public <T> T inTransaction(Function<ReturnDraftStore, T> operation) {
        T result = transactions.execute(status -> operation.apply(this));
        return Objects.requireNonNull(result, "transaction result");
    }

    private Optional<ReturnDraftState> queryOne(String sql, Object... arguments) {
        var rows = jdbc.query(sql, this::mapState, arguments);
        return rows.stream().findFirst();
    }

    private ReturnDraftState mapState(ResultSet row, int rowNumber) throws SQLException {
        var state = new ReturnDraftState();
        String orderId = row.getString("order_id");
        state.id = row.getString("draft_id");
        state.order = new OrderResponse(
                orderId, row.getString("order_status"), row.getString("product"),
                row.getObject("delivered_at", java.time.LocalDate.class), row.getInt("amount_cents"));
        state.eligibility = new ReturnEligibilityResponse(
                orderId, ReturnDecision.valueOf(row.getString("eligibility_decision")),
                row.getBoolean("eligibility_can_apply"), row.getBoolean("eligibility_reason_required"),
                row.getObject("days_since_delivery", Long.class), row.getString("eligibility_reason"));
        state.started = row.getTimestamp("started_at").toInstant();
        state.expires = row.getTimestamp("expires_at").toInstant();
        state.cancelled = row.getBoolean("cancelled");

        String submittedCode = row.getString("submitted_reason_code");
        if (submittedCode != null) {
            state.submitted = new ReviewRequest(
                    row.getString("submitted_reason"), ReasonCode.valueOf(submittedCode));
        }
        String reviewDecision = row.getString("review_decision");
        if (reviewDecision != null) {
            state.result = new ReviewResponse(
                    orderId, ReviewDecision.valueOf(reviewDecision), row.getString("review_reason"));
        }
        String applicationId = row.getString("application_id");
        if (applicationId != null) {
            state.application = new ApplicationResponse(
                    applicationId, orderId, state.order.product(), row.getInt("refund_amount_cents"),
                    row.getString("application_status"), row.getTimestamp("application_created_at").toInstant());
        }
        return state;
    }
}

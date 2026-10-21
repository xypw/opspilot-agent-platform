package dev.opspilot.business.orders;

import static org.assertj.core.api.Assertions.*;
import static dev.opspilot.business.orders.ReturnReviewService.*;
import java.time.*;
import java.util.UUID;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.support.TransactionTemplate;

/** 真 PG 事务验证；只创建、清理本测试 UUID 数据，不修改已有订单。 */
@SpringBootTest(properties = {"opspilot.return-store.backend=postgres", "opspilot.auth.required=false"})
@EnabledIfEnvironmentVariable(named = "RUN_POSTGRES_INTEGRATION", matches = "true")
class PersistentOrderWorkflowIntegrationTest {
    @Autowired OrderRepository orders;
    @Autowired ReturnDraftService drafts;
    @Autowired ReturnEligibilityService eligibility;
    @Autowired JdbcTemplate jdbc;
    @Autowired TransactionTemplate transactions;
    @Autowired Flyway flyway;
    @Autowired Clock clock;
    private String id;
    private final String owner = "IT-owner";

    @BeforeEach
    void fixture() {
        id = "IT-" + UUID.randomUUID();
        jdbc.update("INSERT INTO orders(id, owner_user_id, status, product, delivered_at, amount_cents) "
                + "VALUES (?, ?, 'delivered', '事务测试键盘', ?, 3555)",
                id, owner, LocalDate.now(clock).minusDays(1));
        jdbc.update("INSERT INTO order_return_policies(order_id, category, product_condition, "
                + "exclusion_confirmed_at_purchase, downloaded_or_unsealed, activated, "
                + "merchant_no_reason_days, policy_version) "
                + "VALUES (?, 'STANDARD', 'INTACT', false, false, false, 0, 'IT-v1')", id);
    }

    @AfterEach
    void cleanup() {
        if (id == null) return;
        jdbc.update("DELETE FROM return_applications WHERE order_id = ?", id);
        jdbc.update("DELETE FROM return_drafts WHERE order_id = ?", id);
        jdbc.update("DELETE FROM order_return_policies WHERE order_id = ?", id);
        jdbc.update("DELETE FROM orders WHERE id = ?", id);
    }

    private ReturnDraftService.DraftResponse start() {
        return orders.withLockedOrder(id, owner, drafts::start).orElseThrow();
    }

    @Test
    void ownershipAndPersistenceDoNotDependOnAnInMemoryRepository() {
        var replacement = new PostgresOrderRepository(jdbc, transactions, flyway);
        assertThat(replacement.findByIdForUser(id, owner)).isEqualTo(orders.findByIdForUser(id, owner));
        assertThat(replacement.findByIdForUser(id, "other-user")).isEmpty();
        assertThat(replacement.findByIdForUser(id, "local-demo-user")).isEmpty();
        assertThat(replacement.findByIdForUser(id, " ")).isEmpty();
    }

    @Test
    void changedAmountRejectsOldApprovalAndRefreshRequiresANewDraft() {
        var old = start();
        jdbc.update("UPDATE orders SET amount_cents = 4999 WHERE id = ?", id);
        assertThatThrownBy(() -> orders.withLockedOrder(id, owner, o -> drafts.confirm(o, old.draftId())))
                .hasMessage("ORDER_CHANGED");
        assertThat(applicationCount()).isZero();
        var fresh = orders.withLockedOrder(id, owner, o -> drafts.refresh(o, old.draftId())).orElseThrow();
        assertThat(fresh.draftId()).isNotEqualTo(old.draftId());
        var result = orders.withLockedOrder(id, owner, o -> drafts.confirm(o, fresh.draftId())).orElseThrow();
        assertThat(result.refundAmountCents()).isEqualTo(4999);
    }

    @Test
    void changedPolicyRejectsOldApprovalAndManualReviewCannotBeConfirmed() {
        var old = start();
        jdbc.update("UPDATE order_return_policies SET category='FRESH_PERISHABLE', "
                + "exclusion_confirmed_at_purchase=true, policy_version='IT-v2' WHERE order_id=?", id);
        assertThatThrownBy(() -> orders.withLockedOrder(id, owner, o -> drafts.confirm(o, old.draftId())))
                .hasMessage("ORDER_CHANGED");
        var fresh = orders.withLockedOrder(id, owner, o -> drafts.refresh(o, old.draftId())).orElseThrow();
        var review = orders.withLockedOrder(id, owner, o -> drafts.submit(o, fresh.draftId(),
                new ReviewRequest("商品腐坏", ReasonCode.QUALITY_ISSUE))).orElseThrow();
        assertThat(review.decision()).isEqualTo(ReviewDecision.MANUAL_REVIEW);
        assertThatThrownBy(() -> orders.withLockedOrder(id, owner, o -> drafts.confirm(o, fresh.draftId())))
                .hasMessage("RETURN_NOT_ACCEPTABLE");
        assertThat(applicationCount()).isZero();
    }

    @Test
    void legacyDraftWithoutPolicyFingerprintMustBeRefreshed() {
        var old = start();
        jdbc.update("UPDATE return_drafts SET policy_fingerprint=NULL WHERE order_id=?", id);
        assertThatThrownBy(() -> orders.withLockedOrder(id, owner, o -> drafts.confirm(o, old.draftId())))
                .hasMessage("ORDER_CHANGED");
        var fresh = orders.withLockedOrder(id, owner, o -> drafts.refresh(o, old.draftId())).orElseThrow();
        assertThat(fresh.draftId()).isNotEqualTo(old.draftId());
    }

    @Test
    void incompleteLegacyOrderCannotProduceAZeroAmountRefund() {
        jdbc.update("UPDATE orders SET amount_cents=NULL WHERE id=?", id);
        assertThatThrownBy(() -> orders.findByIdForUser(id, owner))
                .hasMessage("ORDER_DATA_INCOMPLETE");
        assertThat(applicationCount()).isZero();
    }

    @Test
    void duplicateConfirmationAcrossRepositoryInstancesReturnsOriginalReceipt() {
        var draft = start();
        var first = orders.withLockedOrder(id, owner, o -> drafts.confirm(o, draft.draftId())).orElseThrow();
        var replacement = new PostgresOrderRepository(jdbc, transactions, flyway);
        var second = replacement.withLockedOrder(id, owner, o -> drafts.confirm(o, draft.draftId())).orElseThrow();
        assertThat(second).isEqualTo(first);
        assertThat(applicationCount()).isEqualTo(1);
    }

    @Test
    void orderAndPolicyLocksCoverTheEntireConfirmationTransaction() {
        var draft = start();
        orders.withLockedOrder(id, owner, order -> {
            eligibility.evaluate(order); // FOR SHARE 锁住确认依据，直到外层事务结束。
            assertConcurrentUpdateBlocked("UPDATE orders SET amount_cents=8999 WHERE id=?", id);
            assertConcurrentUpdateBlocked(
                    "UPDATE order_return_policies SET policy_version='raced' WHERE order_id=?", id);
            return drafts.confirm(order, draft.draftId());
        }).orElseThrow();
        assertThat(applicationCount()).isEqualTo(1);
        assertThat(orders.findByIdForUser(id, owner).orElseThrow().amountCents()).isEqualTo(3555);
    }

    @Test
    void exceptionAfterCreatingApplicationRollsBackTheWholeOperation() {
        var draft = start();
        assertThatThrownBy(() -> orders.withLockedOrder(id, owner, order -> {
            drafts.confirm(order, draft.draftId());
            throw new IllegalStateException("FAULT_AFTER_INSERT");
        })).hasMessage("FAULT_AFTER_INSERT");
        assertThat(applicationCount()).isZero();
        assertThat(orders.withLockedOrder(id, owner, o -> drafts.confirm(o, draft.draftId()))).isPresent();
        assertThat(applicationCount()).isEqualTo(1);
    }

    private int applicationCount() {
        return jdbc.queryForObject("SELECT COUNT(*) FROM return_applications WHERE order_id=?", Integer.class, id);
    }

    @Test
    void migrationsHandleAnEmptySchemaWithoutResettingDemoDatesOnRerun() {
        checkMigrations(false);
    }

    @Test
    void migrationsPreserveLegacyOrdersWithoutInventingOwnershipOrAmounts() {
        checkMigrations(true);
    }

    private void checkMigrations(boolean legacy) {
        String schema = "order_migration_it_" + UUID.randomUUID().toString().replace("-", "");
        if (!schema.matches("order_migration_it_[a-f0-9]{32}")) throw new IllegalStateException("INVALID_FIXTURE");
        jdbc.execute("CREATE SCHEMA " + schema);
        try {
            if (legacy) {
                jdbc.execute("CREATE TABLE " + schema + ".orders(id text primary key, status text not null "
                        + "CHECK(status IN ('processing','shipped','cancelled')), product text not null, "
                        + "updated_at timestamptz not null default now())");
                jdbc.update("INSERT INTO " + schema + ".orders(id,status,product) VALUES ('O-2001','shipped','旧样例')");
            }
            var migrator = Flyway.configure().dataSource(jdbc.getDataSource())
                    .schemas(schema).defaultSchema(schema).baselineOnMigrate(true).baselineVersion("1")
                    .locations("classpath:db/migration").load();
            migrator.migrate();
            var signedAt = jdbc.queryForObject("SELECT delivered_at FROM " + schema
                    + ".orders WHERE id='O-7101'", LocalDate.class);
            migrator.migrate();
            assertThat(jdbc.queryForObject("SELECT delivered_at FROM " + schema
                    + ".orders WHERE id='O-7101'", LocalDate.class)).isEqualTo(signedAt);
            assertThat(jdbc.queryForObject("SELECT category FROM " + schema
                    + ".order_return_policies WHERE order_id='O-7201'", String.class)).isEqualTo("FRESH_PERISHABLE");
            if (legacy) {
                var preserved = jdbc.queryForMap("SELECT status, owner_user_id, amount_cents FROM "
                        + schema + ".orders WHERE id='O-2001'");
                assertThat(preserved.get("status")).isEqualTo("shipped");
                assertThat(preserved.get("owner_user_id")).isNull();
                assertThat(preserved.get("amount_cents")).isNull();
            }
        } finally {
            jdbc.execute("DROP SCHEMA " + schema + " CASCADE");
        }
    }

    private void assertConcurrentUpdateBlocked(String sql, String orderId) {
        var executor = Executors.newSingleThreadExecutor();
        try {
            var result = executor.submit(() -> transactions.execute(status -> {
                jdbc.execute("SET LOCAL lock_timeout = '300ms'");
                return jdbc.update(sql, orderId);
            }));
            assertThatThrownBy(() -> result.get(5, TimeUnit.SECONDS))
                    .rootCause().isInstanceOf(org.postgresql.util.PSQLException.class)
                    .hasMessageContaining("lock timeout");
        } finally {
            executor.shutdownNow();
        }
    }
}

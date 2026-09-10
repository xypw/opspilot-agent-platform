package dev.opspilot.business.orders;

import static org.assertj.core.api.Assertions.assertThat;
import java.time.*;
import java.util.UUID;
import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.support.TransactionTemplate;

/** 连接真实 PostgreSQL；默认测试不运行，CI 或本地联调时显式开启。 */
@SpringBootTest(properties = "opspilot.return-store.backend=postgres")
@EnabledIfEnvironmentVariable(named = "RUN_POSTGRES_INTEGRATION", matches = "true")
class PostgresReturnDraftIntegrationTest {
    @Autowired ReturnDraftService service;
    @Autowired JdbcTemplate jdbc;
    @Autowired TransactionTemplate transactions;
    @Autowired Flyway flyway;
    @Autowired Clock clock;
    @Autowired ReturnEligibilityService eligibility;
    @Autowired ReturnReviewService reviewer;

    private String orderId;

    @AfterEach
    void removeOnlyThisTestFixture() {
        if (orderId == null) return;
        jdbc.update("DELETE FROM return_applications WHERE order_id = ?", orderId);
        jdbc.update("DELETE FROM return_drafts WHERE order_id = ?", orderId);
    }

    @Test
    void applicationSurvivesAReplacementServiceInstanceAndRetryIsIdempotent() {
        orderId = "IT-" + UUID.randomUUID();
        var order = new OrderResponse(
                orderId, "delivered", "集成测试键盘",
                LocalDate.now(clock).minusDays(1), 3555);

        var draft = service.start(order);
        var firstApplication = service.confirm(order, draft.draftId());

        // 新建业务服务和存储对象，模拟 Java 服务重启后内存已清空。
        var replacementStore = new PostgresReturnDraftStore(jdbc, transactions, flyway);
        var replacementService = new ReturnDraftService(clock, eligibility, reviewer, replacementStore);
        var retriedApplication = replacementService.confirm(order, draft.draftId());

        assertThat(retriedApplication).isEqualTo(firstApplication);
        assertThat(jdbc.queryForObject(
                "SELECT COUNT(*) FROM return_applications WHERE draft_id = ?::uuid",
                Integer.class, draft.draftId())).isEqualTo(1);
    }
}

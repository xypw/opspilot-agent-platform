package dev.opspilot.business.orders;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.LocalDate;
import java.util.Objects;
import java.util.Optional;
import java.util.function.Function;
import org.flywaydb.core.Flyway;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.support.TransactionTemplate;

/** 与草稿共用 DataSource/事务管理器，固定按“订单 -> 草稿”的顺序加锁。 */
@Repository
@ConditionalOnProperty(name = "opspilot.return-store.backend", havingValue = "postgres")
public class PostgresOrderRepository implements OrderRepository {
    private static final String OWNED_ORDER = """
            SELECT id, status, product, delivered_at, amount_cents
              FROM orders WHERE id = ? AND owner_user_id = ?
            """;
    private final JdbcTemplate jdbc;
    private final TransactionTemplate transactions;

    public PostgresOrderRepository(JdbcTemplate jdbc, TransactionTemplate transactions, Flyway migratedSchema) {
        this.jdbc = jdbc;
        this.transactions = transactions;
    }

    @Override
    public Optional<OrderResponse> findByIdForUser(String orderId, String userId) {
        return query(OWNED_ORDER, orderId, userId);
    }

    @Override
    public <T> Optional<T> withLockedOrder(
            String orderId, String userId, Function<OrderResponse, T> operation) {
        // 草稿存储的 REQUIRED 事务加入此事务；订单锁持续到申请提交/回滚结束。
        return Objects.requireNonNull(transactions.execute(status ->
                query(OWNED_ORDER + " FOR UPDATE", orderId, userId).map(operation)));
    }

    private Optional<OrderResponse> query(String sql, String orderId, String userId) {
        if (userId == null || userId.isBlank()) return Optional.empty();
        // PG 模式没有 local-demo-user 绕过：所有请求都必须匹配持久化归属。
        return jdbc.query(sql, this::mapOrder, orderId, userId).stream().findFirst();
    }

    private OrderResponse mapOrder(ResultSet row, int rowNumber) throws SQLException {
        Integer amount = row.getObject("amount_cents", Integer.class);
        String product = row.getString("product");
        String status = row.getString("status");
        LocalDate delivered = row.getObject("delivered_at", LocalDate.class);
        if (amount == null || amount < 0 || product == null || product.isBlank()
                || ("delivered".equals(status) && delivered == null)) {
            throw new DataIntegrityViolationException("ORDER_DATA_INCOMPLETE");
        }
        return new OrderResponse(row.getString("id"), status, product, delivered, amount);
    }
}

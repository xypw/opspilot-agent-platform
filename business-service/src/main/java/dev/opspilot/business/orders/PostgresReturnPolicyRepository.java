package dev.opspilot.business.orders;

import org.flywaydb.core.Flyway;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

/** 商品规则随订单持久保存，缺失规则不回退到内存演示数据。 */
@Repository
@ConditionalOnProperty(name = "opspilot.return-store.backend", havingValue = "postgres")
public class PostgresReturnPolicyRepository implements ReturnPolicyRepository {
    private final JdbcTemplate jdbc;
    public PostgresReturnPolicyRepository(JdbcTemplate jdbc, Flyway migratedSchema) { this.jdbc = jdbc; }

    public OrderReturnPolicy find(String orderId) {
        var rows = jdbc.query("SELECT * FROM order_return_policies WHERE order_id = ? FOR SHARE",
                (row, number) -> new OrderReturnPolicy(
                        OrderReturnPolicy.Category.valueOf(row.getString("category")),
                        OrderReturnPolicy.Condition.valueOf(row.getString("product_condition")),
                        row.getObject("exclusion_confirmed_at_purchase", Boolean.class),
                        row.getObject("downloaded_or_unsealed", Boolean.class),
                        row.getObject("activated", Boolean.class),
                        row.getObject("merchant_no_reason_days", Integer.class),
                        row.getString("policy_version")), orderId);
        return rows.stream().findFirst().orElseGet(OrderReturnPolicy::unknown);
    }
}

package dev.opspilot.business.orders;

import java.time.Clock;
import java.time.LocalDate;
import java.util.Map;
import java.util.Optional;
import org.springframework.stereotype.Repository;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;

/** 明确使用虚构、只读的内存数据；这还不是数据库订单仓库。 */
@Repository
@ConditionalOnProperty(name = "opspilot.return-store.backend", havingValue = "memory", matchIfMissing = true)
public class DemoOrderRepository implements OrderRepository {
    private final Map<String, OrderResponse> orders;
    private final Map<String, String> owners;

    public DemoOrderRepository(Clock businessClock) {
        LocalDate sevenDaysAgo = LocalDate.now(businessClock).minusDays(7);
        orders = Map.of(
                "O-2001", new OrderResponse(
                        "O-2001", "delivered", "机械键盘", sevenDaysAgo, 39900),
                "O-2002", new OrderResponse(
                        "O-2002", "processing", "无线鼠标", null, 12900),
                "O-2003", new OrderResponse(
                        "O-2003", "cancelled", "显示器", null, 159900),
                "O-7101", new OrderResponse(
                        "O-7101", "delivered", "演示机械键盘", sevenDaysAgo, 39900),
                "O-7102", new OrderResponse(
                        "O-7102", "processing", "演示无线鼠标", null, 12900),
                "O-7103", new OrderResponse(
                        "O-7103", "cancelled", "演示显示器", null, 159900),
                "O-7201", new OrderResponse(
                        "O-7201", "delivered", "演示鲜活香蕉", LocalDate.now(businessClock).minusDays(1), 1990),
                "O-7202", new OrderResponse(
                        "O-7202", "delivered", "演示定制刻字水杯", LocalDate.now(businessClock).minusDays(1), 9900));
        owners = Map.of("O-2001", "U-1001", "O-2002", "U-1002", "O-2003", "U-1001",
                "O-7101", "U-1001", "O-7102", "U-1002", "O-7103", "U-1001",
                "O-7201", "U-1001", "O-7202", "U-1001");
    }

    public Optional<OrderResponse> findById(String orderId) {
        return Optional.ofNullable(orders.get(orderId));
    }

    public Optional<OrderResponse> findByIdForUser(String orderId, String userId) {
        if ("local-demo-user".equals(userId)) return findById(orderId);
        return userId.equals(owners.get(orderId)) ? findById(orderId) : Optional.empty();
    }
}

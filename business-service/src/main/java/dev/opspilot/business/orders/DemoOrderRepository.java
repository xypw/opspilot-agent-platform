package dev.opspilot.business.orders;

import java.time.Clock;
import java.time.LocalDate;
import java.util.Map;
import java.util.Optional;
import org.springframework.stereotype.Repository;

/** 明确使用虚构、只读的内存数据；这还不是数据库订单仓库。 */
@Repository
public class DemoOrderRepository {
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
                        "O-2003", "cancelled", "显示器", null, 159900));
        owners = Map.of("O-2001", "U-1001", "O-2002", "U-1002", "O-2003", "U-1001");
    }

    public Optional<OrderResponse> findById(String orderId) {
        return Optional.ofNullable(orders.get(orderId));
    }

    public Optional<OrderResponse> findByIdForUser(String orderId, String userId) {
        if ("local-demo-user".equals(userId)) return findById(orderId);
        return userId.equals(owners.get(orderId)) ? findById(orderId) : Optional.empty();
    }
}

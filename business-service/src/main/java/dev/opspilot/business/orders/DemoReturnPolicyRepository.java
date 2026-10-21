package dev.opspilot.business.orders;

import java.util.Map;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Repository;
import static dev.opspilot.business.orders.OrderReturnPolicy.*;

/** 仅为明确标注的虚构演示订单提供商品事实，不按商品名称猜测类别。 */
@Repository
@ConditionalOnProperty(name = "opspilot.return-store.backend", havingValue = "memory", matchIfMissing = true)
public class DemoReturnPolicyRepository implements ReturnPolicyRepository {
    private final Map<String, OrderReturnPolicy> policies = Map.of(
            "O-2001", standard(), "O-7101", standard(),
            "O-7201", new OrderReturnPolicy(Category.FRESH_PERISHABLE, Condition.INTACT,
                    true, false, false, 0, "demo-fresh-v1"),
            "O-7202", new OrderReturnPolicy(Category.CUSTOM_MADE, Condition.INTACT,
                    true, false, false, 0, "demo-custom-v1"));

    public OrderReturnPolicy find(String orderId) {
        return policies.getOrDefault(orderId, unknown());
    }
}

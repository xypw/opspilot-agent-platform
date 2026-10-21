package dev.opspilot.business.orders;

import java.util.Optional;
import java.util.function.Function;

/** 订单事实及归属查询；持久化实现将整次草稿操作绑定到锁定的订单。 */
public interface OrderRepository {
    Optional<OrderResponse> findByIdForUser(String orderId, String userId);

    default <T> Optional<T> withLockedOrder(
            String orderId, String userId, Function<OrderResponse, T> operation) {
        return findByIdForUser(orderId, userId).map(operation);
    }
}

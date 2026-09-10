package dev.opspilot.business.orders;

import java.util.Optional;
import java.util.function.Function;

/**
 * 退货流程的持久化边界。
 *
 * <p>业务服务只认识这个接口，不关心数据保存在内存还是 PostgreSQL。</p>
 */
interface ReturnDraftStore {
    Optional<ReturnDraftState> findByOrderId(String orderId);

    Optional<ReturnDraftState> find(String orderId, String draftId);

    long count();

    boolean insertIfAbsent(ReturnDraftState draft);

    boolean replace(String oldDraftId, ReturnDraftState refreshedDraft);

    void save(ReturnDraftState draft);

    /** PostgreSQL 实现覆盖此方法，使一次业务操作处于同一事务中。 */
    default <T> T inTransaction(Function<ReturnDraftStore, T> operation) {
        return operation.apply(this);
    }
}

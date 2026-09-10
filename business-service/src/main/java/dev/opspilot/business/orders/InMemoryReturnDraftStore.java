package dev.opspilot.business.orders;

import java.util.HashMap;
import java.util.Map;
import java.util.Optional;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

/** 快速单元测试和离线教学使用的内存存储。 */
@Component
@ConditionalOnProperty(name = "opspilot.return-store.backend", havingValue = "memory", matchIfMissing = true)
final class InMemoryReturnDraftStore implements ReturnDraftStore {
    private final Map<String, ReturnDraftState> byOrder = new HashMap<>();

    @Override
    public synchronized Optional<ReturnDraftState> findByOrderId(String orderId) {
        return Optional.ofNullable(byOrder.get(orderId));
    }

    @Override
    public synchronized Optional<ReturnDraftState> find(String orderId, String draftId) {
        var draft = byOrder.get(orderId);
        return draft != null && draft.id.equals(draftId) ? Optional.of(draft) : Optional.empty();
    }

    @Override
    public synchronized long count() {
        return byOrder.size();
    }

    @Override
    public synchronized boolean insertIfAbsent(ReturnDraftState draft) {
        return byOrder.putIfAbsent(draft.order.id(), draft) == null;
    }

    @Override
    public synchronized boolean replace(String oldDraftId, ReturnDraftState refreshedDraft) {
        var current = byOrder.get(refreshedDraft.order.id());
        if (current == null || !current.id.equals(oldDraftId)) return false;
        byOrder.put(refreshedDraft.order.id(), refreshedDraft);
        return true;
    }

    @Override
    public synchronized void save(ReturnDraftState draft) {
        byOrder.put(draft.order.id(), draft);
    }
}

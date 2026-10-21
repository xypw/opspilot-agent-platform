package dev.opspilot.business.orders;

import dev.opspilot.business.security.ServiceAuthenticationFilter;
import jakarta.servlet.http.HttpServletRequest;
import java.util.Map;
import java.util.function.Function;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/orders/{orderId}/return-draft")
public class ReturnDraftController {
    private final OrderRepository repository;
    private final ReturnDraftService drafts;
    public ReturnDraftController(OrderRepository repository, ReturnDraftService drafts) {
        this.repository = repository;
        this.drafts = drafts;
    }
    private <T> T withOrder(String id, HttpServletRequest request, Function<OrderResponse, T> operation) {
        if (!id.matches("O-[0-9]{4}")) throw new ReturnDraftService.DraftError(400, "INVALID_ORDER_ID");
        String userId = ServiceAuthenticationFilter.currentUser(request).userId();
        return repository.withLockedOrder(id, userId, operation).orElseThrow(
                () -> new ReturnDraftService.DraftError(404, "ORDER_NOT_FOUND"));
    }
    @PostMapping
    public ReturnDraftService.DraftResponse start(
            @PathVariable String orderId, HttpServletRequest request) {
        // 开始时间只取服务器时钟，不接受用户提供的时间。
        return withOrder(orderId, request, drafts::start);
    }
    @GetMapping("/{draftId}")
    public ReturnDraftService.DraftResponse get(@PathVariable String orderId,
            @PathVariable String draftId, HttpServletRequest request) {
        return withOrder(orderId, request, order -> drafts.get(orderId, draftId));
    }
    @PostMapping("/{draftId}/reason")
    public ReturnReviewService.ReviewResponse submit(@PathVariable String orderId, @PathVariable String draftId,
            @RequestBody ReturnReviewService.ReviewRequest body, HttpServletRequest request) {
        return withOrder(orderId, request, order -> drafts.submit(order, draftId, body));
    }
    @PostMapping("/{draftId}/confirm")
    public ReturnDraftService.ApplicationResponse confirm(
            @PathVariable String orderId, @PathVariable String draftId,
            HttpServletRequest request) {
        return withOrder(orderId, request, order -> drafts.confirm(order, draftId));
    }
    @PostMapping("/{draftId}/cancel")
    public ReturnDraftService.DraftResponse cancel(
            @PathVariable String orderId, @PathVariable String draftId,
            HttpServletRequest request) {
        return withOrder(orderId, request, order -> drafts.cancel(orderId, draftId));
    }
    @PostMapping("/{draftId}/refresh")
    public ReturnDraftService.DraftResponse refresh(
            @PathVariable String orderId, @PathVariable String draftId,
            HttpServletRequest request) {
        return withOrder(orderId, request, order -> drafts.refresh(order, draftId));
    }
    @ExceptionHandler(ReturnDraftService.DraftError.class)
    public ResponseEntity<?> handle(ReturnDraftService.DraftError error) {
        return ResponseEntity.status(error.status).body(Map.of("code", error.getMessage()));
    }
    @ExceptionHandler(IllegalArgumentException.class)
    public ResponseEntity<?> invalid() {
        return ResponseEntity.badRequest().body(Map.of("code", "INVALID_RETURN_REASON"));
    }
}

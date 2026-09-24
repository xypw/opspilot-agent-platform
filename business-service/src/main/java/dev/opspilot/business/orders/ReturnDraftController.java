package dev.opspilot.business.orders;

import dev.opspilot.business.security.ServiceAuthenticationFilter;
import jakarta.servlet.http.HttpServletRequest;
import java.util.Map;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/orders/{orderId}/return-draft")
public class ReturnDraftController {
    private final DemoOrderRepository repository;
    private final ReturnDraftService drafts;
    public ReturnDraftController(DemoOrderRepository repository, ReturnDraftService drafts) {
        this.repository = repository;
        this.drafts = drafts;
    }
    private OrderResponse order(String id, HttpServletRequest request) {
        if (!id.matches("O-[0-9]{4}")) throw new ReturnDraftService.DraftError(400, "INVALID_ORDER_ID");
        String userId = ServiceAuthenticationFilter.currentUser(request).userId();
        return repository.findByIdForUser(id, userId).orElseThrow(
                () -> new ReturnDraftService.DraftError(404, "ORDER_NOT_FOUND"));
    }
    @PostMapping
    public ReturnDraftService.DraftResponse start(
            @PathVariable String orderId, HttpServletRequest request) {
        // 开始时间只取服务器时钟，不接受用户提供的时间。
        return drafts.start(order(orderId, request));
    }
    @GetMapping("/{draftId}")
    public ReturnDraftService.DraftResponse get(@PathVariable String orderId,
            @PathVariable String draftId, HttpServletRequest request) {
        order(orderId, request);
        return drafts.get(orderId, draftId);
    }
    @PostMapping("/{draftId}/reason")
    public ReturnReviewService.ReviewResponse submit(@PathVariable String orderId, @PathVariable String draftId,
            @RequestBody ReturnReviewService.ReviewRequest body, HttpServletRequest request) {
        return drafts.submit(order(orderId, request), draftId, body);
    }
    @PostMapping("/{draftId}/confirm")
    public ReturnDraftService.ApplicationResponse confirm(
            @PathVariable String orderId, @PathVariable String draftId,
            HttpServletRequest request) {
        return drafts.confirm(order(orderId, request), draftId);
    }
    @PostMapping("/{draftId}/cancel")
    public ReturnDraftService.DraftResponse cancel(
            @PathVariable String orderId, @PathVariable String draftId,
            HttpServletRequest request) {
        order(orderId, request);
        return drafts.cancel(orderId, draftId);
    }
    @PostMapping("/{draftId}/refresh")
    public ReturnDraftService.DraftResponse refresh(
            @PathVariable String orderId, @PathVariable String draftId,
            HttpServletRequest request) {
        return drafts.refresh(order(orderId, request), draftId);
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

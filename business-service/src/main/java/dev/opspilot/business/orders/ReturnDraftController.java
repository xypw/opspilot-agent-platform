package dev.opspilot.business.orders;

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
    private OrderResponse order(String id) {
        if (!id.matches("O-[0-9]{4}")) throw new ReturnDraftService.DraftError(400, "INVALID_ORDER_ID");
        return repository.findById(id).orElseThrow(
                () -> new ReturnDraftService.DraftError(404, "ORDER_NOT_FOUND"));
    }
    @PostMapping
    public ReturnDraftService.DraftResponse start(@PathVariable String orderId) {
        // 开始时间只取服务器时钟，不接受用户提供的时间。
        return drafts.start(order(orderId));
    }
    @GetMapping("/{draftId}")
    public ReturnDraftService.DraftResponse get(@PathVariable String orderId, @PathVariable String draftId) {
        order(orderId);
        return drafts.get(orderId, draftId);
    }
    @PostMapping("/{draftId}/reason")
    public ReturnReviewService.ReviewResponse submit(@PathVariable String orderId, @PathVariable String draftId,
            @RequestBody ReturnReviewService.ReviewRequest request) {
        return drafts.submit(order(orderId), draftId, request);
    }
    @PostMapping("/{draftId}/confirm")
    public ReturnDraftService.ApplicationResponse confirm(
            @PathVariable String orderId, @PathVariable String draftId) {
        return drafts.confirm(order(orderId), draftId);
    }
    @PostMapping("/{draftId}/cancel")
    public ReturnDraftService.DraftResponse cancel(
            @PathVariable String orderId, @PathVariable String draftId) {
        order(orderId);
        return drafts.cancel(orderId, draftId);
    }
    @PostMapping("/{draftId}/refresh")
    public ReturnDraftService.DraftResponse refresh(
            @PathVariable String orderId, @PathVariable String draftId) {
        return drafts.refresh(order(orderId), draftId);
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

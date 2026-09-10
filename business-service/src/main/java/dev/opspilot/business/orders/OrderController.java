package dev.opspilot.business.orders;

import java.util.Map;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** HTTP 边界只负责输入检查和状态码映射，不调用模型、不决定售后资格。 */
@RestController
@RequestMapping("/api/orders")
public class OrderController {
    private final DemoOrderRepository repository;
    private final ReturnEligibilityService returnEligibilityService;
    private final ReturnReviewService returnReviewService;

    public OrderController(
            DemoOrderRepository repository,
            ReturnEligibilityService returnEligibilityService,
            ReturnReviewService returnReviewService) {
        this.repository = repository;
        this.returnEligibilityService = returnEligibilityService;
        this.returnReviewService = returnReviewService;
    }

    @GetMapping("/{orderId}")
    public ResponseEntity<?> getOrder(@PathVariable String orderId) {
        // 当前演示契约只接受 O- 加四位数字；Java 不能盲信 Python 的输入。
        if (!orderId.matches("O-[0-9]{4}")) {
            return ResponseEntity.badRequest().body(Map.of("code", "INVALID_ORDER_ID"));
        }
        var order = repository.findById(orderId);
        if (order.isEmpty()) {
            // 查无业务记录与服务崩溃是不同结果，调用方必须区分。
            return ResponseEntity.status(404).body(Map.of("code", "ORDER_NOT_FOUND"));
        }
        return ResponseEntity.ok(order.get());
    }

    @PostMapping("/{orderId}/return-review")
    public ResponseEntity<?> reviewReturn(@PathVariable String orderId,
            @RequestBody ReturnReviewService.ReviewRequest request) {
        if (!orderId.matches("O-[0-9]{4}")) {
            return ResponseEntity.badRequest().body(Map.of("code", "INVALID_ORDER_ID"));
        }
        var order = repository.findById(orderId);
        if (order.isEmpty()) {
            return ResponseEntity.status(404).body(Map.of("code", "ORDER_NOT_FOUND"));
        }
        try {
            return ResponseEntity.ok(returnReviewService.review(order.get(), request));
        } catch (IllegalArgumentException error) {
            return ResponseEntity.badRequest().body(Map.of("code", "INVALID_RETURN_REASON"));
        }
    }

    @GetMapping("/{orderId}/return-eligibility")
    public ResponseEntity<?> getReturnEligibility(@PathVariable String orderId) {
        if (!orderId.matches("O-[0-9]{4}")) {
            return ResponseEntity.badRequest().body(Map.of("code", "INVALID_ORDER_ID"));
        }
        var order = repository.findById(orderId);
        if (order.isEmpty()) {
            return ResponseEntity.status(404).body(Map.of("code", "ORDER_NOT_FOUND"));
        }
        return ResponseEntity.ok(returnEligibilityService.evaluate(order.get()));
    }
}

package dev.opspilot.business.orders;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.Map;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.client.TestRestTemplate;

/** 使用随机端口启动真实 HTTP 服务，不占用正在演示的 8081 端口。 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class OrderApiTest {
    @Autowired
    private TestRestTemplate http;

    @Test
    void reviewEndpointReturnsBusinessRejectionForCancelledOrder() {
        var response = http.postForEntity("/api/orders/O-2003/return-review",
                Map.of("return_reason", "损坏", "reason_code", "QUALITY_ISSUE"), Map.class);
        assertThat(response.getStatusCode().value()).isEqualTo(200);
        assertThat(response.getBody()).isEqualTo(Map.of(
                "order_id", "O-2003", "decision", "REJECTED", "reason", "ORDER_CANCELLED"));
    }

    @Test
    void reviewRejectsBlankReasonAndUnknownCategory() {
        for (var payload : java.util.List.of(
                Map.of("return_reason", " ", "reason_code", "OTHER"),
                Map.of("return_reason", "故障", "reason_code", "INVENTED"))) {
            var response = http.postForEntity("/api/orders/O-2001/return-review", payload, Map.class);
            assertThat(response.getStatusCode().value()).isEqualTo(400);
        }
    }

    @Test
    void returnsExistingOrderWithStableJsonFields() {
        var response = http.getForEntity("/api/orders/O-2001", Map.class);
        assertThat(response.getStatusCode().value()).isEqualTo(200);
        assertThat(response.getBody()).isEqualTo(Map.of(
                "id", "O-2001",
                "status", "delivered",
                "product", "机械键盘",
                "delivered_at", "2026-08-31",
                "amount_cents", 39900));
    }

    @Test
    void usesRequestedIdRatherThanAHardcodedOrder() {
        var response = http.getForEntity("/api/orders/O-2003", OrderResponse.class);
        assertThat(response.getStatusCode().value()).isEqualTo(200);
        assertThat(response.getBody()).isEqualTo(
                new OrderResponse("O-2003", "cancelled", "显示器", null, 159900));
    }

    @Test
    void returns404ForMissingOrder() {
        var response = http.getForEntity("/api/orders/O-9999", Map.class);
        assertThat(response.getStatusCode().value()).isEqualTo(404);
        assertThat(response.getBody()).isEqualTo(Map.of("code", "ORDER_NOT_FOUND"));
    }

    @Test
    void rejectsTicketIdAtJavaBoundary() {
        var response = http.getForEntity("/api/orders/T-1001", Map.class);
        assertThat(response.getStatusCode().value()).isEqualTo(400);
        assertThat(response.getBody()).isEqualTo(Map.of("code", "INVALID_ORDER_ID"));
    }

    @Test
    void rejectsWritesToTheReadOnlyEndpoint() {
        var response = http.postForEntity("/api/orders/O-2001", Map.of("status", "cancelled"), Map.class);
        assertThat(response.getStatusCode().value()).isEqualTo(405);
        var order = http.getForObject("/api/orders/O-2001", OrderResponse.class);
        assertThat(order.status()).isEqualTo("delivered");
        assertThat(order.amountCents()).isEqualTo(39900);
    }

    @Test
    void returnsStableNotAllowedDecisionForUndeliveredOrder() {
        var response = http.getForEntity(
                "/api/orders/O-2002/return-eligibility", ReturnEligibilityResponse.class);

        assertThat(response.getStatusCode().value()).isEqualTo(200);
        assertThat(response.getBody()).isEqualTo(new ReturnEligibilityResponse(
                "O-2002", ReturnDecision.NOT_ALLOWED, false, false, null,
                "ORDER_NOT_DELIVERED"));
    }
}

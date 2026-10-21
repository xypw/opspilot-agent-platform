package dev.opspilot.business.orders;

import static org.assertj.core.api.Assertions.*;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.client.TestRestTemplate;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT,
        properties = {"opspilot.auth.required=false", "opspilot.return-store.backend=memory"})
class CategoryReturnPolicyApiTest {
    @Autowired TestRestTemplate http;

    @Test
    void freshAndCustomOrdersAreNotClassifiedAsOrdinaryGoods() {
        for (String id : java.util.List.of("O-7201", "O-7202")) {
            var result = http.getForObject("/api/orders/" + id + "/return-eligibility", Map.class);
            assertThat(result.get("decision")).isEqualTo("REASON_REQUIRED");
            assertThat(result.get("reason")).isEqualTo("NO_REASON_CATEGORY_EXCLUDED");
            assertThat(result).doesNotContainKey("policyFingerprint");
        }
    }
    @Test
    void extraUserFieldsCannotOverrideTrustedCategory() {
        var result = http.postForObject("/api/orders/O-7201/return-review",
                Map.of("return_reason", "不喜欢", "reason_code", "PERSONAL_PREFERENCE",
                        "category", "STANDARD", "merchant_no_reason_days", 365), Map.class);
        assertThat(result.get("decision")).isEqualTo("REJECTED");
        assertThat(result.get("reason")).isEqualTo("NO_REASON_CATEGORY_EXCLUDED");
    }
    @Test
    void freshQualityClaimIsRoutedToEvidenceReview() {
        var result = http.postForObject("/api/orders/O-7201/return-review",
                Map.of("return_reason", "到货腐坏", "reason_code", "QUALITY_ISSUE"), Map.class);
        assertThat(result.get("decision")).isEqualTo("MANUAL_REVIEW");
    }
    @Test
    void qualityReviewCannotBeOverriddenByASeparateConfirmRequest() {
        String base = "/api/orders/O-7101/return-draft";
        var draft = http.postForObject(base, null, ReturnDraftService.DraftResponse.class);
        http.postForObject(base + "/" + draft.draftId() + "/reason",
                Map.of("return_reason", "按键故障", "reason_code", "QUALITY_ISSUE"), Map.class);
        var result = http.postForEntity(base + "/" + draft.draftId() + "/confirm", null, Map.class);
        assertThat(result.getStatusCode().value()).isEqualTo(409);
        assertThat(result.getBody()).isEqualTo(Map.of("code", "RETURN_NOT_ACCEPTABLE"));
    }
}

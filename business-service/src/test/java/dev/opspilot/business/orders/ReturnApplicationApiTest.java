package dev.opspilot.business.orders;

import static org.assertj.core.api.Assertions.assertThat;
import java.time.*;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.context.annotation.*;
import org.springframework.test.annotation.DirtiesContext;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@Import(ReturnApplicationApiTest.Config.class)
@DirtiesContext(classMode = DirtiesContext.ClassMode.BEFORE_EACH_TEST_METHOD)
class ReturnApplicationApiTest {
    @TestConfiguration
    static class Config {
        // 订单 O-2001 在该服务器日期恰好是签收第7天，可以直接进入确认。
        @Bean @Primary Clock testClock() {
            return Clock.fixed(Instant.parse("2026-09-07T04:00:00Z"), ZoneId.of("Asia/Shanghai"));
        }
    }

    @Autowired TestRestTemplate http;

    @Test
    void confirmationCreatesOneIdempotentApplication() {
        String base = "/api/orders/O-2001/return-draft";
        var draft = http.postForObject(base, null, ReturnDraftService.DraftResponse.class);

        var first = http.postForObject(base + "/" + draft.draftId() + "/confirm", null,
                ReturnDraftService.ApplicationResponse.class);
        var retry = http.postForObject(base + "/" + draft.draftId() + "/confirm", null,
                ReturnDraftService.ApplicationResponse.class);

        assertThat(first).isEqualTo(retry);
        assertThat(first.orderId()).isEqualTo("O-2001");
        assertThat(first.product()).isEqualTo("机械键盘");
        assertThat(first.refundAmountCents()).isEqualTo(39900);
        assertThat(first.status()).isEqualTo("SUBMITTED");
    }

    @Test
    void cancellationPreventsLaterConfirmation() {
        String base = "/api/orders/O-2001/return-draft";
        var draft = http.postForObject(base, null, ReturnDraftService.DraftResponse.class);

        var cancelled = http.postForObject(base + "/" + draft.draftId() + "/cancel", null,
                ReturnDraftService.DraftResponse.class);
        var rejected = http.postForEntity(base + "/" + draft.draftId() + "/confirm", null, Map.class);

        assertThat(cancelled.status()).isEqualTo("CANCELLED");
        assertThat(rejected.getStatusCode().value()).isEqualTo(409);
        assertThat(rejected.getBody()).isEqualTo(Map.of("code", "DRAFT_CANCELLED"));
    }
}

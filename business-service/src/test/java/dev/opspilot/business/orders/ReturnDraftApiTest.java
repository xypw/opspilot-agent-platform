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

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@Import(ReturnDraftApiTest.Config.class)
class ReturnDraftApiTest {
    static class MutableClock extends Clock {
        volatile Instant now = Instant.parse("2026-09-15T15:30:00Z");
        public Instant instant() { return now; }
        public ZoneId getZone() { return ZoneId.of("Asia/Shanghai"); }
        public Clock withZone(ZoneId zone) { return Clock.fixed(now, zone); }
    }
    @TestConfiguration
    static class Config {
        @Bean @Primary MutableClock testClock() { return new MutableClock(); }
    }
    @Autowired TestRestTemplate http;
    @Autowired MutableClock clock;

    @Test
    void httpUsesServerTimeAndReturns410AtDeadline() {
        String base = "/api/orders/O-2001/return-draft";
        var response = http.postForEntity(base, Map.of("started_at", "2099-01-01T00:00:00Z"),
                                          ReturnDraftService.DraftResponse.class);
        assertThat(response.getStatusCode().value()).isEqualTo(200);
        var draft = response.getBody();
        assertThat(draft.startedAt()).isEqualTo(clock.now);
        assertThat(draft.expiresAt()).isEqualTo(clock.now.plusSeconds(3600));
        assertThat(http.postForObject(base, null, ReturnDraftService.DraftResponse.class)).isEqualTo(draft);
        clock.now = clock.now.plusSeconds(3600);
        assertThat(http.getForObject(base + "/" + draft.draftId(),
                ReturnDraftService.DraftResponse.class).status()).isEqualTo("EXPIRED");
        var rejected = http.postForEntity(base + "/" + draft.draftId() + "/reason",
                Map.of("return_reason", "按键失灵", "reason_code", "QUALITY_ISSUE"), Map.class);
        assertThat(rejected.getStatusCode().value()).isEqualTo(410);
        assertThat(rejected.getBody()).isEqualTo(Map.of("code", "DRAFT_EXPIRED"));
    }
}

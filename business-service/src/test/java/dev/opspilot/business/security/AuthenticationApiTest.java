package dev.opspilot.business.security;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.Map;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.http.*;

@SpringBootTest(
        webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT,
        properties = {
            "opspilot.auth.required=true",
            "opspilot.auth.tokens={\"user-a-token-00000001\":{\"user_id\":\"U-1001\",\"roles\":[\"customer\"]},\"user-b-token-00000002\":{\"user_id\":\"U-1002\",\"roles\":[\"customer\"]}}",
            "opspilot.internal-service-token=internal-service-token-0001"
        })
class AuthenticationApiTest {
    @LocalServerPort int port;
    @Autowired TestRestTemplate http;

    @Test
    void rejectsMissingAuthenticationAndDirectBrowserBypass() {
        var missing = http.getForEntity(url("/api/orders/O-2001"), Map.class);
        assertThat(missing.getStatusCode()).isEqualTo(HttpStatus.UNAUTHORIZED);

        HttpHeaders bearerOnly = new HttpHeaders();
        bearerOnly.setBearerAuth("user-a-token-00000001");
        var bypass = http.exchange(
                url("/api/orders/O-2001"), HttpMethod.GET,
                new HttpEntity<>(bearerOnly), Map.class);
        assertThat(bypass.getStatusCode()).isEqualTo(HttpStatus.FORBIDDEN);
        assertThat(bypass.getBody()).containsEntry("code", "TRUSTED_SERVICE_REQUIRED");
    }

    @Test
    void isolatesOrdersByAuthenticatedUser() {
        var own = http.exchange(
                url("/api/orders/O-2001"), HttpMethod.GET,
                new HttpEntity<>(trustedHeaders("user-a-token-00000001")), Map.class);
        var foreign = http.exchange(
                url("/api/orders/O-2001"), HttpMethod.GET,
                new HttpEntity<>(trustedHeaders("user-b-token-00000002")), Map.class);
        assertThat(own.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(foreign.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
    }

    private HttpHeaders trustedHeaders(String token) {
        HttpHeaders headers = new HttpHeaders();
        headers.setBearerAuth(token);
        headers.set("X-OpsPilot-Service-Token", "internal-service-token-0001");
        return headers;
    }

    private String url(String path) {
        return "http://127.0.0.1:" + port + path;
    }
}

package dev.opspilot.business.security;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.sql.ResultSet;
import java.time.Instant;
import java.util.Base64;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.mock.web.MockFilterChain;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.test.util.ReflectionTestUtils;

class PortalIdentityTest {
    private static final String SECRET = "portal-identity-unit-secret-00001";
    private final ObjectMapper mapper = new ObjectMapper();
    private final JdbcTemplate jdbc = mock(JdbcTemplate.class);
    private ServiceAuthenticationFilter filter;

    @BeforeEach
    void setUp() {
        filter = new ServiceAuthenticationFilter(mapper, true, "{}", SECRET);
        ReflectionTestUtils.setField(filter, "jdbc", jdbc);
    }

    private Map<String, Object> claims() {
        long now = Instant.now().getEpochSecond();
        return new HashMap<>(Map.of("sub", "U-registered", "iat", now, "exp", now + 30,
                "iss", "opspilot-portal", "aud", "opspilot-business"));
    }

    private byte[] mac(byte[] key, byte[] data) throws Exception {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(key, "HmacSHA256"));
        return mac.doFinal(data);
    }

    private String signed(Map<String, Object> claims) throws Exception {
        var encoder = Base64.getUrlEncoder().withoutPadding();
        String data = encoder.encodeToString(mapper.writeValueAsBytes(Map.of("alg", "HS256", "typ", "JWT")))
                + "." + encoder.encodeToString(mapper.writeValueAsBytes(claims));
        byte[] key = mac(SECRET.getBytes(StandardCharsets.UTF_8),
                "opspilot-portal-identity-v1".getBytes(StandardCharsets.UTF_8));
        return data + "." + encoder.encodeToString(mac(key, data.getBytes(StandardCharsets.UTF_8)));
    }

    private MockHttpServletResponse call(String bearer, boolean trusted) throws Exception {
        var request = new MockHttpServletRequest("GET", "/api/orders/O-8000");
        request.addHeader("Authorization", "Bearer " + bearer);
        if (trusted) request.addHeader("X-OpsPilot-Service-Token", SECRET);
        var response = new MockHttpServletResponse();
        var chain = new MockFilterChain();
        filter.doFilter(request, response, chain);
        if (response.getStatus() == 200) assertThat(chain.getRequest()).isSameAs(request);
        else assertThat(chain.getRequest()).isNull();
        return response;
    }

    @Test
    void activeIdentityIsAcceptedAndRevocationIsCheckedAgain() throws Exception {
        var row = mock(ResultSet.class);
        when(row.getString("user_id")).thenReturn("U-registered");
        when(jdbc.query(anyString(), any(RowMapper.class), eq("U-registered")))
                .thenAnswer(invocation -> List.of(invocation.<RowMapper<?>>getArgument(1).mapRow(row, 0)))
                .thenReturn(List.of());
        String token = signed(claims());
        assertThat(call(token, true).getStatus()).isEqualTo(200);
        assertThat(call(token, true).getStatus()).isEqualTo(401);
        verify(jdbc, times(2)).query(anyString(), any(RowMapper.class), eq("U-registered"));
    }

    @Test
    void signedButDisabledIdentityIsRejectedAfterDatabaseLookup() throws Exception {
        when(jdbc.query(anyString(), any(RowMapper.class), eq("U-registered"))).thenReturn(List.of());
        assertThat(call(signed(claims()), true).getStatus()).isEqualTo(401);
        verify(jdbc).query(eq("SELECT user_id FROM portal_accounts WHERE user_id=? AND active"),
                any(RowMapper.class), eq("U-registered"));
    }

    @Test
    void alteredPayloadCannotChooseAnotherUser() throws Exception {
        String token = signed(claims());
        var changed = claims();
        changed.put("sub", "U-admin");
        String forged = token.split("\\.")[0] + "."
                + Base64.getUrlEncoder().withoutPadding().encodeToString(mapper.writeValueAsBytes(changed))
                + "." + token.split("\\.")[2];
        assertThat(call(forged, true).getStatus()).isEqualTo(401);
        verifyNoInteractions(jdbc);
    }

    @Test
    void rejectsExpiredFutureOverlongAndWrongAudienceAssertionsBeforeLookup() throws Exception {
        long now = Instant.now().getEpochSecond();
        for (var changes : List.of(Map.<String, Object>of("iat", now - 40, "exp", now - 10),
                Map.<String, Object>of("iat", now + 60, "exp", now + 90),
                Map.<String, Object>of("exp", now + 300),
                Map.<String, Object>of("aud", "other-service"),
                Map.<String, Object>of("iss", "other-issuer"),
                Map.<String, Object>of("exp", "9999999999"))) {
            var payload = claims();
            payload.putAll(changes);
            assertThat(call(signed(payload), true).getStatus()).isEqualTo(401);
        }
        verifyNoInteractions(jdbc);
    }

    @Test
    void validSignatureStillRequiresTrustedService() throws Exception {
        assertThat(call(signed(claims()), false).getStatus()).isEqualTo(403);
        verifyNoInteractions(jdbc);
    }
}

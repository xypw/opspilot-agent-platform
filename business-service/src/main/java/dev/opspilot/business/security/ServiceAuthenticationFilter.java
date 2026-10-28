package dev.opspilot.business.security;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Map;
import java.util.Set;
import java.util.Base64;
import java.time.Instant;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * 双重认证边界：Bearer 证明最终用户，内部服务令牌证明请求来自受控 Python 编排层。
 *
 * <p>模型、PDF 和浏览器都拿不到内部令牌，因此不能绕过人工确认直接调用 Java 写接口。</p>
 */
@Component
public final class ServiceAuthenticationFilter extends OncePerRequestFilter {
    public static final String USER_ATTRIBUTE =
            ServiceAuthenticationFilter.class.getName() + ".user";
    private static final TypeReference<Map<String, TokenDetails>> TOKEN_MAP =
            new TypeReference<>() {};

    private final ObjectMapper mapper;
    private final boolean required;
    private final String configuredTokens;
    private final String internalServiceToken;
    @Autowired(required = false)
    private JdbcTemplate jdbc;

    public ServiceAuthenticationFilter(
            ObjectMapper mapper,
            @Value("${opspilot.auth.required:true}") boolean required,
            @Value("${opspilot.auth.tokens:}") String configuredTokens,
            @Value("${opspilot.internal-service-token:}") String internalServiceToken) {
        this.mapper = mapper;
        this.required = required;
        this.configuredTokens = configuredTokens;
        this.internalServiceToken = internalServiceToken;
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        return !required || "/health".equals(request.getRequestURI());
    }

    @Override
    protected void doFilterInternal(
            HttpServletRequest request,
            HttpServletResponse response,
            FilterChain chain) throws ServletException, IOException {
        if (configuredTokens.isBlank() || internalServiceToken.length() < 16) {
            writeError(response, 503, "AUTH_NOT_CONFIGURED");
            return;
        }
        String authorization = request.getHeader("Authorization");
        String bearer = authorization != null && authorization.startsWith("Bearer ")
                ? authorization.substring(7) : "";
        if (bearer.isBlank()) {
            writeError(response, 401, "INVALID_ACCESS_TOKEN");
            return;
        }
        // Verify the trusted caller before interpreting any end-user assertion.
        String serviceToken = request.getHeader("X-OpsPilot-Service-Token");
        if (!constantTimeEquals(serviceToken, internalServiceToken)) {
            writeError(response, 403, "TRUSTED_SERVICE_REQUIRED");
            return;
        }
        TokenDetails details = findUser(bearer);
        if (details == null) details = findPortalUser(bearer);
        if (details == null) {
            writeError(response, 401, "INVALID_ACCESS_TOKEN");
            return;
        }
        request.setAttribute(USER_ATTRIBUTE, new AuthenticatedUser(
                details.userId(), details.roles() == null ? Set.of() : Set.copyOf(details.roles())));
        chain.doFilter(request, response);
    }

    private TokenDetails findUser(String bearer) {
        if (bearer.isBlank()) return null;
        try {
            Map<String, TokenDetails> tokens = mapper.readValue(configuredTokens, TOKEN_MAP);
            for (var entry : tokens.entrySet()) {
                if (entry.getKey().length() >= 16 && constantTimeEquals(bearer, entry.getKey())) {
                    TokenDetails details = entry.getValue();
                    return details != null && details.userId() != null && !details.userId().isBlank()
                            ? details : null;
                }
            }
            return null;
        } catch (IOException error) {
            return null;
        }
    }

    private static byte[] mac(byte[] key, byte[] message) throws Exception {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(key, "HmacSHA256"));
        return mac.doFinal(message);
    }

    private TokenDetails findPortalUser(String bearer) {
        if (jdbc == null || bearer.length() > 2048) return null;
        try {
            String[] parts = bearer.split("\\.", -1);
            if (parts.length != 3) return null;
            var decoder = Base64.getUrlDecoder();
            var header = mapper.readTree(decoder.decode(parts[0]));
            if (!"HS256".equals(header.path("alg").asText()) || !"JWT".equals(header.path("typ").asText())) return null;
            byte[] key = mac(internalServiceToken.getBytes(StandardCharsets.UTF_8),
                    "opspilot-portal-identity-v1".getBytes(StandardCharsets.UTF_8));
            if (!MessageDigest.isEqual(mac(key, (parts[0]+"."+parts[1]).getBytes(StandardCharsets.UTF_8)),
                    decoder.decode(parts[2]))) return null;
            var payload = mapper.readTree(decoder.decode(parts[1]));
            long now = Instant.now().getEpochSecond();
            if (!payload.path("exp").isIntegralNumber() || !payload.path("iat").isIntegralNumber()
                    || payload.path("exp").asLong() <= now || payload.path("iat").asLong() > now + 5
                    || payload.path("exp").asLong()-payload.path("iat").asLong() > 30
                    || !"opspilot-portal".equals(payload.path("iss").asText())
                    || !"opspilot-business".equals(payload.path("aud").asText())) return null;
            String userId = payload.path("sub").asText();
            if (userId.isBlank() || userId.length() > 100) return null;
            // Revocation is checked on every business call, including in-flight agents.
            return jdbc.query("SELECT user_id FROM portal_accounts WHERE user_id=? AND active",
                    (row, n) -> new TokenDetails(row.getString("user_id"), Set.of("customer")), userId)
                    .stream().findFirst().orElse(null);
        } catch (Exception error) {
            return null;
        }
    }

    private static boolean constantTimeEquals(String provided, String expected) {
        if (provided == null) return false;
        return MessageDigest.isEqual(
                provided.getBytes(StandardCharsets.UTF_8),
                expected.getBytes(StandardCharsets.UTF_8));
    }

    private void writeError(HttpServletResponse response, int status, String code)
            throws IOException {
        response.setStatus(status);
        response.setContentType("application/json");
        mapper.writeValue(response.getOutputStream(), Map.of("code", code));
    }

    private record TokenDetails(
            @com.fasterxml.jackson.annotation.JsonProperty("user_id") String userId,
            Set<String> roles) {}

    public static AuthenticatedUser currentUser(HttpServletRequest request) {
        Object value = request.getAttribute(USER_ATTRIBUTE);
        if (value instanceof AuthenticatedUser user) return user;
        // 仅在显式关闭鉴权的本地测试模式可到达。
        return new AuthenticatedUser("local-demo-user", Set.of("customer", "knowledge_admin"));
    }
}

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
        TokenDetails details = findUser(bearer);
        if (details == null) {
            writeError(response, 401, "INVALID_ACCESS_TOKEN");
            return;
        }
        String serviceToken = request.getHeader("X-OpsPilot-Service-Token");
        if (!constantTimeEquals(serviceToken, internalServiceToken)) {
            writeError(response, 403, "TRUSTED_SERVICE_REQUIRED");
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

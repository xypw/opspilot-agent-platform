package dev.opspilot.business.security;

import java.util.Set;

/** 只由服务端认证过滤器创建，业务请求体和模型都不能指定 userId。 */
public record AuthenticatedUser(String userId, Set<String> roles) {}

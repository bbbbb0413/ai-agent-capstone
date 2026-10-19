package com.opslens.backend.auth;

import jakarta.servlet.http.Cookie;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.security.oauth2.server.resource.web.BearerTokenResolver;
import org.springframework.security.oauth2.server.resource.web.DefaultBearerTokenResolver;

/**
 * Authorization 헤더가 없으면 HttpOnly 쿠키에서 JWT를 읽는다.
 * 브라우저 EventSource(SSE)는 헤더를 실을 수 없어서 쿠키가 필요하다.
 */
public class CookieBearerTokenResolver implements BearerTokenResolver {

    private final DefaultBearerTokenResolver headerResolver = new DefaultBearerTokenResolver();

    @Override
    public String resolve(HttpServletRequest request) {
        // 로그인 요청은 만료된 쿠키가 남아 있어도 인증 없이 통과해야 한다
        if ("/api/auth/login".equals(request.getRequestURI())) {
            return null;
        }
        String token = headerResolver.resolve(request);
        if (token != null || request.getCookies() == null) {
            return token;
        }
        for (Cookie cookie : request.getCookies()) {
            if (AuthController.TOKEN_COOKIE.equals(cookie.getName())) {
                return cookie.getValue();
            }
        }
        return null;
    }
}

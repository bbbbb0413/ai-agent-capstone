package com.opslens.backend.auth;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.Cookie;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletRequestWrapper;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import org.springframework.http.HttpHeaders;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Authorization 헤더가 없으면 HttpOnly 쿠키의 JWT를 Authorization 헤더로 옮겨 다음 필터에 넘긴다.
 * 브라우저 EventSource(SSE)는 헤더를 실을 수 없어서 쿠키가 필요하다.
 *
 * CSRF 필터 뒤에 둔다. 리소스 서버는 Bearer 토큰이 있는 요청을 CSRF 검사에서 빼는데, 쿠키에서 토큰을 읽는
 * BearerTokenResolver를 쓰면 쿠키로 인증한 요청까지 빠진다. CSRF 필터는 원래 요청(쿠키만 있음)을 보고,
 * 인증 필터는 옮긴 헤더를 본다.
 */
public class CookieTokenFilter extends OncePerRequestFilter {

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        String token = request.getHeader(HttpHeaders.AUTHORIZATION) == null ? cookieToken(request) : null;
        chain.doFilter(token == null ? request : withBearer(request, token), response);
    }

    private static String cookieToken(HttpServletRequest request) {
        // 로그인 요청은 만료된 쿠키가 남아 있어도 인증 없이 통과해야 한다
        if ("/api/auth/login".equals(request.getRequestURI()) || request.getCookies() == null) {
            return null;
        }
        for (Cookie cookie : request.getCookies()) {
            if (AuthController.TOKEN_COOKIE.equals(cookie.getName())) {
                return cookie.getValue();
            }
        }
        return null;
    }

    private static HttpServletRequest withBearer(HttpServletRequest request, String token) {
        return new HttpServletRequestWrapper(request) {
            @Override
            public String getHeader(String name) {
                return HttpHeaders.AUTHORIZATION.equalsIgnoreCase(name) ? "Bearer " + token : super.getHeader(name);
            }
        };
    }
}

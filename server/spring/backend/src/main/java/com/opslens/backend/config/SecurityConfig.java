package com.opslens.backend.config;

import static java.nio.charset.StandardCharsets.UTF_8;

import com.nimbusds.jose.jwk.source.ImmutableSecret;
import com.opslens.backend.auth.CookieTokenFilter;
import com.opslens.backend.auth.ServiceTokenFilter;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.util.function.Supplier;
import javax.crypto.SecretKey;
import javax.crypto.spec.SecretKeySpec;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.annotation.Order;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configurers.AbstractHttpConfigurer;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.crypto.factory.PasswordEncoderFactories;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.oauth2.jose.jws.MacAlgorithm;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.security.oauth2.jwt.JwtEncoder;
import org.springframework.security.oauth2.jwt.NimbusJwtDecoder;
import org.springframework.security.oauth2.jwt.NimbusJwtEncoder;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationConverter;
import org.springframework.security.oauth2.server.resource.authentication.JwtGrantedAuthoritiesConverter;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.UsernamePasswordAuthenticationFilter;
import org.springframework.security.web.csrf.CookieCsrfTokenRepository;
import org.springframework.security.web.csrf.CsrfFilter;
import org.springframework.security.web.csrf.CsrfToken;
import org.springframework.security.web.csrf.CsrfTokenRequestAttributeHandler;

@Configuration
public class SecurityConfig {

    /** /internal/** : docker network 안의 서비스만 호출한다. 사용자 JWT가 아니라 서비스 토큰으로 인증한다. */
    @Bean
    @Order(1)
    SecurityFilterChain internalChain(HttpSecurity http, OpsLensProperties props) throws Exception {
        http.securityMatcher("/internal/**")
                .csrf(AbstractHttpConfigurer::disable)
                .sessionManagement(s -> s.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .addFilterBefore(new ServiceTokenFilter(props), UsernamePasswordAuthenticationFilter.class)
                .authorizeHttpRequests(a -> a.anyRequest().permitAll());
        return http.build();
    }

    /** Slack 콜백은 요청 본문 서명으로 인증한다. */
    @Bean
    @Order(2)
    SecurityFilterChain slackInteractionChain(HttpSecurity http) throws Exception {
        // 이 한 경로만 Slack HMAC 서명으로 인증한다. Controller가 raw body와 시각을 검증한다.
        http.securityMatcher("/api/slack/interactions")
                .csrf(AbstractHttpConfigurer::disable)
                .sessionManagement(s -> s.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .authorizeHttpRequests(a -> a.requestMatchers(org.springframework.http.HttpMethod.POST, "/api/slack/interactions")
                        .permitAll().anyRequest().denyAll());
        return http.build();
    }

    /**
     * 화면 API는 JWT(Authorization 헤더 또는 HttpOnly 쿠키)로 인증한다.
     *
     * CSRF: 쿠키로 인증한 변경 요청은 XSRF-TOKEN 쿠키 값을 X-XSRF-TOKEN 헤더로 되돌려 보내야 한다 (SPA 방식).
     * 인증 쿠키는 SameSite=Strict라 다른 사이트에서는 실리지 않지만, 같은 사이트의 다른 하위 도메인이나 폼 형식
     * 요청(첨부 업로드)까지 막으려고 토큰을 더한다 (docs/specs/SPEC-limitations-fixes.md 2단계).
     * 검사에서 빼는 요청:
     *   - 로그인: 아직 토큰 쿠키를 받기 전이다
     *   - Authorization 헤더 요청: 리소스 서버가 뺀다. 브라우저가 자동으로 붙이지 않는 헤더라 다른 사이트가 위조할 수 없다
     * 쿠키의 JWT는 CSRF 검사 뒤에 헤더로 옮긴다 (CookieTokenFilter). 그래야 쿠키 요청이 Bearer 요청으로 빠지지 않는다.
     */
    @Bean
    @Order(3)
    SecurityFilterChain apiChain(HttpSecurity http) throws Exception {
        CookieCsrfTokenRepository csrfTokens = CookieCsrfTokenRepository.withHttpOnlyFalse();
        csrfTokens.setCookieCustomizer(cookie -> cookie.sameSite("Strict").path("/"));
        http.csrf(c -> c.csrfTokenRepository(csrfTokens).csrfTokenRequestHandler(new EagerCsrfTokenHandler())
                        .ignoringRequestMatchers("/api/auth/login"))
                .addFilterAfter(new CookieTokenFilter(), CsrfFilter.class)
                .sessionManagement(s -> s.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .authorizeHttpRequests(a -> a
                        // /error: 오류 응답(403 메시지 등)이 인증 실패(401)로 덮이지 않게 연다
                        .requestMatchers("/api/auth/login", "/api/auth/logout", "/actuator/health", "/error")
                        .permitAll()
                        .anyRequest().authenticated())
                .oauth2ResourceServer(o -> o
                        .jwt(j -> j.jwtAuthenticationConverter(jwtAuthenticationConverter())));
        return http.build();
    }

    /**
     * 요청마다 토큰을 읽어 XSRF-TOKEN 쿠키가 첫 응답(로그인 포함)부터 내려가게 한다. 기본은 토큰을 쓸 때까지 미뤄서
     * 화면이 변경 요청을 보내기 전에 쿠키가 없을 수 있다. 헤더 값은 쿠키 값 그대로 비교한다(화면에 토큰을 렌더링하지 않으므로
     * BREACH 대비 마스킹은 쓰지 않는다).
     */
    static final class EagerCsrfTokenHandler extends CsrfTokenRequestAttributeHandler {
        @Override
        public void handle(HttpServletRequest request, HttpServletResponse response, Supplier<CsrfToken> token) {
            super.handle(request, response, token);
            token.get();
        }
    }

    @Bean
    SecretKey jwtKey(OpsLensProperties props) {
        return new SecretKeySpec(props.jwtSecret().getBytes(UTF_8), "HmacSHA256");
    }

    @Bean
    JwtDecoder jwtDecoder(SecretKey jwtKey) {
        return NimbusJwtDecoder.withSecretKey(jwtKey).macAlgorithm(MacAlgorithm.HS256).build();
    }

    @Bean
    JwtEncoder jwtEncoder(SecretKey jwtKey) {
        return new NimbusJwtEncoder(new ImmutableSecret<>(jwtKey));
    }

    @Bean
    PasswordEncoder passwordEncoder() {
        return PasswordEncoderFactories.createDelegatingPasswordEncoder();
    }

    private JwtAuthenticationConverter jwtAuthenticationConverter() {
        var authorities = new JwtGrantedAuthoritiesConverter();
        authorities.setAuthoritiesClaimName("role");
        authorities.setAuthorityPrefix("ROLE_");
        var converter = new JwtAuthenticationConverter();
        converter.setJwtGrantedAuthoritiesConverter(authorities);
        return converter;
    }
}

package com.opslens.backend.config;

import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.cookie;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.opslens.backend.auth.AuthController;
import jakarta.servlet.http.Cookie;
import java.time.Instant;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.security.oauth2.jose.jws.MacAlgorithm;
import org.springframework.security.oauth2.jwt.JwsHeader;
import org.springframework.security.oauth2.jwt.JwtClaimsSet;
import org.springframework.security.oauth2.jwt.JwtEncoder;
import org.springframework.security.oauth2.jwt.JwtEncoderParameters;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

/** 화면 API의 CSRF 검사: 쿠키 인증 변경 요청은 토큰이 있어야 하고, 로그인과 Bearer 헤더 요청은 검사하지 않는다. */
@WebMvcTest(controllers = CsrfProtectionTest.Probe.class)
@Import({SecurityConfig.class, CsrfProtectionTest.Probe.class})
@EnableConfigurationProperties(OpsLensProperties.class)
@TestPropertySource(properties = "opslens.jwt-secret=test-secret-test-secret-test-secret-0123")
class CsrfProtectionTest {

    @RestController
    static class Probe {
        @GetMapping("/api/probe")
        String read() {
            return "ok";
        }

        @PostMapping("/api/probe")
        String change() {
            return "ok";
        }

        @PostMapping("/api/auth/login")
        String login() {
            return "ok";
        }
    }

    @Autowired
    MockMvc mvc;

    @Autowired
    JwtEncoder encoder;

    /** 화면과 같이 로그인 JWT를 HttpOnly 쿠키로 싣는다. spring-security-test의 jwt()는 CSRF 필터를 건너뛰어 쓰지 않는다 */
    private Cookie session() {
        var claims = JwtClaimsSet.builder().subject("engineer").claim("role", "MSP_ENGINEER")
                .issuedAt(Instant.now()).expiresAt(Instant.now().plusSeconds(60)).build();
        return new Cookie(AuthController.TOKEN_COOKIE, encoder.encode(
                JwtEncoderParameters.from(JwsHeader.with(MacAlgorithm.HS256).build(), claims)).getTokenValue());
    }

    @Test
    void cookieAuthenticatedChangeWithoutTokenIsForbidden() throws Exception {
        mvc.perform(post("/api/probe").cookie(session())).andExpect(status().isForbidden());
    }

    @Test
    void tokenFromTheCookieRoundTripsInTheHeader() throws Exception {
        MvcResult read = mvc.perform(get("/api/probe").cookie(session()))
                .andExpect(status().isOk())
                .andExpect(cookie().exists("XSRF-TOKEN"))
                .andExpect(cookie().httpOnly("XSRF-TOKEN", false))   // 화면 스크립트가 읽어 헤더에 싣는다
                .andExpect(cookie().sameSite("XSRF-TOKEN", "Strict"))
                .andReturn();
        Cookie token = read.getResponse().getCookie("XSRF-TOKEN");
        assertNotNull(token);
        mvc.perform(post("/api/probe").cookie(session(), token).header("X-XSRF-TOKEN", token.getValue()))
                .andExpect(status().isOk());
        mvc.perform(post("/api/probe").cookie(session(), token).header("X-XSRF-TOKEN", "forged"))
                .andExpect(status().isForbidden());
    }

    @Test
    void loginNeedsNoToken() throws Exception {
        mvc.perform(post("/api/auth/login")).andExpect(status().isOk());
    }

    @Test
    void bearerHeaderRequestNeedsNoToken() throws Exception {
        String bearer = session().getValue();
        mvc.perform(post("/api/probe").header("Authorization", "Bearer " + bearer)).andExpect(status().isOk());
    }
}

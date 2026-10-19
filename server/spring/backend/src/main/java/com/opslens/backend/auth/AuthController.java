package com.opslens.backend.auth;

import com.opslens.backend.config.OpsLensProperties;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import java.time.Instant;
import java.util.List;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseCookie;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.oauth2.jose.jws.MacAlgorithm;
import org.springframework.security.oauth2.jwt.JwsHeader;
import org.springframework.security.oauth2.jwt.JwtClaimsSet;
import org.springframework.security.oauth2.jwt.JwtEncoder;
import org.springframework.security.oauth2.jwt.JwtEncoderParameters;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

@RestController
@RequestMapping("/api/auth")
public class AuthController {

    public static final String TOKEN_COOKIE = "opslens_token";

    record LoginRequest(@NotBlank String username, @NotBlank String password) {}

    record LoginResponse(String token, String username, String role, List<String> tenants) {}

    private record UserRow(long id, String password, String role, String tenantId) {}

    private final JdbcClient jdbc;
    private final PasswordEncoder passwordEncoder;
    private final JwtEncoder jwtEncoder;
    private final OpsLensProperties props;

    public AuthController(JdbcClient jdbc, PasswordEncoder passwordEncoder, JwtEncoder jwtEncoder,
            OpsLensProperties props) {
        this.jdbc = jdbc;
        this.passwordEncoder = passwordEncoder;
        this.jwtEncoder = jwtEncoder;
        this.props = props;
    }

    @PostMapping("/login")
    ResponseEntity<LoginResponse> login(@Valid @RequestBody LoginRequest request) {
        UserRow user = jdbc.sql("SELECT id, password, role, tenant_id FROM users WHERE username = ?")
                .param(request.username())
                .query((rs, n) -> new UserRow(rs.getLong("id"), rs.getString("password"),
                        rs.getString("role"), rs.getString("tenant_id")))
                .optional()
                .filter(u -> passwordEncoder.matches(request.password(), u.password()))
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.UNAUTHORIZED, "invalid credentials"));

        List<String> tenants = switch (user.role()) {
            case CurrentUser.MSP_ADMIN -> jdbc.sql("SELECT id FROM tenants ORDER BY id").query(String.class).list();
            case CurrentUser.MSP_ENGINEER -> jdbc.sql(
                            "SELECT tenant_id FROM user_tenants WHERE user_id = ? ORDER BY tenant_id")
                    .param(user.id()).query(String.class).list();
            default -> List.of(user.tenantId());
        };

        Instant now = Instant.now();
        JwtClaimsSet claims = JwtClaimsSet.builder()
                .subject(request.username())
                .issuedAt(now)
                .expiresAt(now.plus(props.jwtTtl()))
                .claim("uid", user.id())
                .claim("role", user.role())
                .claim("tenants", tenants)
                .build();
        String token = jwtEncoder.encode(
                JwtEncoderParameters.from(JwsHeader.with(MacAlgorithm.HS256).build(), claims)).getTokenValue();

        ResponseCookie cookie = ResponseCookie.from(TOKEN_COOKIE, token)
                .httpOnly(true).sameSite("Strict").path("/").maxAge(props.jwtTtl()).build();
        return ResponseEntity.ok()
                .header(HttpHeaders.SET_COOKIE, cookie.toString())
                .body(new LoginResponse(token, request.username(), user.role(), tenants));
    }
}

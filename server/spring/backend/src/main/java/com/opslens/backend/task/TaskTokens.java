package com.opslens.backend.task;

import com.nimbusds.jose.JOSEException;
import com.nimbusds.jose.JWSAlgorithm;
import com.nimbusds.jose.JWSHeader;
import com.nimbusds.jose.crypto.RSASSASigner;
import com.nimbusds.jose.crypto.RSASSAVerifier;
import com.nimbusds.jose.jwk.JWKSet;
import com.nimbusds.jose.jwk.RSAKey;
import com.nimbusds.jose.jwk.gen.RSAKeyGenerator;
import com.nimbusds.jwt.JWTClaimsSet;
import com.nimbusds.jwt.SignedJWT;
import java.text.ParseException;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.Date;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ResponseStatusException;

/**
 * 작업 토큰: 실행 중인 작업 하나의 고객사를 담은 서명 토큰 (SPEC-limitations-fixes 3단계).
 * agent가 실행 임대를 잡은 직후 받아 MCP Server에 넘기고, backend와 rag-mcp는 이 토큰의 고객사만 믿는다.
 *
 * 공개키 서명(RS256)이다. rag-mcp는 공개키로 검증만 하므로, rag-mcp가 뚫려도 토큰을 만들 수 없다.
 * 키 쌍은 기동할 때 만든다. 다시 시작하면 그 전에 발급한 토큰은 무효가 되고, agent는 다음 실행 구간에서 새로 받는다.
 */
@Component
public class TaskTokens {

    /** 실행 한 구간(한도 120초 + 회복 확인 대기, 약 4분)보다 넉넉하다. 실행·재개마다 새로 받는다 */
    static final Duration TTL = Duration.ofMinutes(15);
    static final String ISSUER = "opslens-backend";
    static final String AUDIENCE = "opslens-task";
    /** 토큰을 주는 상태. 승인·반영을 기다리는 동안 agent는 실행하지 않고, 끝난 작업은 다시 실행하지 않는다 */
    static final Set<String> ACTIVE = Set.of(TaskRecord.QUEUED, TaskRecord.RUNNING);

    public record Issued(String token, Instant expiresAt) {}

    public record Claims(UUID taskId, String tenantId) {}

    private final RSAKey key;
    private final Clock clock;

    @Autowired
    public TaskTokens() {
        this(Clock.systemUTC());
    }

    TaskTokens(Clock clock) {
        try {
            this.key = new RSAKeyGenerator(2048).keyID(UUID.randomUUID().toString()).generate();
        } catch (JOSEException e) {
            throw new IllegalStateException("작업 토큰 서명 키를 만들지 못했다", e);
        }
        this.clock = clock;
    }

    public Issued issue(TaskRecord task) {
        if (!ACTIVE.contains(task.status())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT,
                    "작업이 실행 중이 아니라 작업 토큰을 줄 수 없다 (상태 %s)".formatted(task.status()));
        }
        Instant now = clock.instant();
        Instant expires = now.plus(TTL);
        JWTClaimsSet claims = new JWTClaimsSet.Builder().issuer(ISSUER).audience(AUDIENCE)
                .subject(task.id().toString()).claim("tenant", task.tenantId())
                .issueTime(Date.from(now)).expirationTime(Date.from(expires)).build();
        SignedJWT jwt = new SignedJWT(new JWSHeader.Builder(JWSAlgorithm.RS256).keyID(key.getKeyID()).build(), claims);
        try {
            jwt.sign(new RSASSASigner(key));
        } catch (JOSEException e) {
            throw new IllegalStateException("작업 토큰에 서명하지 못했다", e);
        }
        return new Issued(jwt.serialize(), expires);
    }

    /** 서명·발급자·대상·만료를 확인한다. 하나라도 맞지 않으면 401. */
    public Claims verify(String token) {
        if (token == null || token.isBlank()) {
            throw unauthorized("작업 토큰(X-Task-Token)이 없다");
        }
        try {
            SignedJWT jwt = SignedJWT.parse(token);
            if (!jwt.verify(new RSASSAVerifier(key.toRSAPublicKey()))) {
                throw unauthorized("작업 토큰 서명이 맞지 않다");
            }
            JWTClaimsSet claims = jwt.getJWTClaimsSet();
            Date expires = claims.getExpirationTime();
            if (!ISSUER.equals(claims.getIssuer()) || !List.of(AUDIENCE).equals(claims.getAudience())
                    || expires == null || !expires.toInstant().isAfter(clock.instant())) {
                throw unauthorized("작업 토큰이 만료됐거나 이 용도가 아니다");
            }
            return new Claims(UUID.fromString(claims.getSubject()), claims.getStringClaim("tenant"));
        } catch (ParseException | JOSEException | IllegalArgumentException e) {
            throw unauthorized("작업 토큰을 읽지 못했다");
        }
    }

    /** 공개키 목록 (JWKS). rag-mcp가 받아 검증에 쓴다 */
    public Map<String, Object> jwks() {
        return new JWKSet(key.toPublicJWK()).toJSONObject();
    }

    private static ResponseStatusException unauthorized(String message) {
        return new ResponseStatusException(HttpStatus.UNAUTHORIZED, message);
    }
}

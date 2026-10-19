package com.opslens.demo;

import static java.nio.charset.StandardCharsets.UTF_8;

import java.security.MessageDigest;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

/**
 * 장애 주입 API. 시연 스크립트(scripts/scenario-*.sh)만 호출한다. 서비스 토큰이 필요하다.
 * 조치는 여기가 아니라 Kubernetes API(파드 재시작, 확장, 롤백)로 한다.
 */
@RestController
@RequestMapping("/admin")
public class AdminController {

    record FaultRequest(FaultState.Fault fault) {}

    private static final Logger log = LoggerFactory.getLogger(AdminController.class);

    private final FaultState faults;
    private final String adminToken;

    public AdminController(FaultState faults, @Value("${demo.admin-token}") String adminToken) {
        this.faults = faults;
        this.adminToken = adminToken;
    }

    @GetMapping("/faults")
    Map<String, Object> status(@RequestHeader("Authorization") String authorization) {
        authorize(authorization);
        return state();
    }

    @PostMapping("/faults")
    Map<String, Object> inject(@RequestHeader("Authorization") String authorization,
            @RequestBody FaultRequest request) {
        authorize(authorization);
        faults.inject(request.fault());
        log.info("fault injected: {}", request.fault());
        return state();
    }

    @DeleteMapping("/faults")
    Map<String, Object> clear(@RequestHeader("Authorization") String authorization) {
        authorize(authorization);
        faults.clear();
        log.info("fault cleared");
        return state();
    }

    private Map<String, Object> state() {
        return Map.of("fault", faults.fault(), "poolSize", faults.poolSize());
    }

    private void authorize(String authorization) {
        String expected = "Bearer " + adminToken;
        if (adminToken.isBlank() || !MessageDigest.isEqual(expected.getBytes(UTF_8), authorization.getBytes(UTF_8))) {
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED);
        }
    }
}

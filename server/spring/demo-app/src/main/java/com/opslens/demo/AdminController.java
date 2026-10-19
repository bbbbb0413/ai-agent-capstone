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
 * 관리 API. 장애 주입은 시연 스크립트가, 조치(풀 조정·재시작·장애 해제)는 backend가 호출한다.
 * docker network 안에서만 열리고 서비스 토큰이 필요하다.
 */
@RestController
@RequestMapping("/admin")
public class AdminController {

    record FaultRequest(FaultState.Fault fault) {}

    record PoolRequest(int size) {}

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

    @PostMapping("/pool")
    Map<String, Object> resizePool(@RequestHeader("Authorization") String authorization,
            @RequestBody PoolRequest request) {
        authorize(authorization);
        faults.resizePool(request.size());
        log.info("connection pool resized to {}", request.size());
        return state();
    }

    @PostMapping("/restart")
    Map<String, Object> restart(@RequestHeader("Authorization") String authorization) {
        authorize(authorization);
        // 실제 재시작 대신 상태를 초기화한다. 커넥션 풀 고갈은 재시작으로 일시 해소된다
        if (faults.fault() == FaultState.Fault.DB_POOL_EXHAUSTION) {
            faults.clear();
        }
        log.info("application restarted");
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

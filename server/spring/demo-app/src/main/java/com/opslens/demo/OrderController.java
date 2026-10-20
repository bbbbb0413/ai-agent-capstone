package com.opslens.demo;

import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import org.springframework.context.annotation.Profile;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 외부 사용자의 주문 요청. 부하 발생기(traffic 네임스페이스)가 호출한다. 자체 생성 트래픽과 같은 처리 슬롯을 쓴다.
 * 요청 스레드(Tomcat)에서 슬롯을 기다리지 않고 주문 전용 스레드로 넘긴다. 과부하에서 Tomcat 스레드가 모두 슬롯
 * 대기에 묶이면 /actuator/health·/actuator/prometheus가 응답하지 못해 파드가 Service에서 빠지고 메트릭이 끊겼다.
 */
@RestController
@Profile("!loadgen")
public class OrderController {

    private final OrderService orders;
    private final ExecutorService executor = Executors.newVirtualThreadPerTaskExecutor();

    public OrderController(OrderService orders) {
        this.orders = orders;
    }

    @PostMapping("/orders")
    CompletableFuture<ResponseEntity<Map<String, Object>>> order() {
        long received = System.nanoTime();   // 응답 시간은 받은 시각부터 잰다
        return CompletableFuture.supplyAsync(() -> orders.process(received), executor)
                .thenApply(ok -> ResponseEntity.status(ok ? 200 : 503).body(Map.<String, Object>of("ok", ok)));
    }
}

package com.opslens.backend.slack;

import jakarta.annotation.PreDestroy;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Component
public class SlackRelay {
    private final SlackDeliveryService deliveries;
    private final SlackConnectionService connections;
    private final SlackWebhookClient client;
    private final ExecutorService sender = Executors.newSingleThreadExecutor(Thread.ofVirtual().factory());
    private final AtomicBoolean running = new AtomicBoolean();

    public SlackRelay(SlackDeliveryService deliveries, SlackConnectionService connections, SlackWebhookClient client) {
        this.deliveries = deliveries;
        this.connections = connections;
        this.client = client;
    }

    @Scheduled(fixedDelay = 1000, initialDelay = 3000)
    void poll() {
        if (!running.compareAndSet(false, true)) return;
        sender.execute(() -> {
            try { sendPending(); }
            finally { running.set(false); }
        });
    }

    @PreDestroy
    void stop() { sender.shutdownNow(); }

    void sendPending() {
        for (var claimed : deliveries.claim()) {
            var connection = connections.find(claimed.tenantId()).orElse(null);
            if (connection == null || !connection.enabled() || connection.version() != claimed.connectionVersion()) {
                deliveries.finish(claimed, new SlackWebhookClient.Result("CANCELLED", "Slack 설정 변경으로 취소됨", 0));
                continue;
            }
            String webhook;
            try {
                webhook = connections.webhook(connection);
            } catch (RuntimeException e) {
                deliveries.finish(claimed, new SlackWebhookClient.Result("FAILED", "Slack 연결 정보를 다시 저장해야 한다", 0));
                continue;
            }
            deliveries.finish(claimed, claimed.trackingEnabled()
                    ? client.sendInteractive(webhook, claimed.message(), claimed.id()) : client.send(webhook, claimed.message()));
        }
    }
}

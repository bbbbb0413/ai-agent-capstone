package com.opslens.backend.slack;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.stereotype.Component;

@Component
public class SlackWebhookClient {
    public record Result(String status, String error, int retryAfterSeconds) {}
    private final HttpClient http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(5))
            .followRedirects(HttpClient.Redirect.NEVER).build();
    private final ObjectMapper json;

    public SlackWebhookClient(ObjectMapper json) { this.json = json; }

    static Map<String, Object> payload(String message) {
        String fallback = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;");
        return Map.of("text", fallback, "blocks", List.of(Map.of("type", "section", "text",
                Map.of("type", "plain_text", "text", message, "emoji", false))));
    }

    public Result send(String webhook, String message) {
        return sendPayload(webhook, payload(message));
    }

    public Result sendInteractive(String webhook, String message, UUID deliveryId) {
        return sendPayload(webhook, SlackTrackingService.payload(message, deliveryId, "WAITING", null, null));
    }

    private Result sendPayload(String webhook, Map<String, Object> payload) {
        try {
            SlackConnectionService.validateWebhook(webhook);
            HttpRequest request = HttpRequest.newBuilder(URI.create(webhook)).timeout(Duration.ofSeconds(10))
                    .header("Content-Type", "application/json")
                    .POST(HttpRequest.BodyPublishers.ofString(json.writeValueAsString(payload))).build();
            HttpResponse<String> response = http.send(request, HttpResponse.BodyHandlers.ofString());
            return classify(response.statusCode(), response.body(), response.headers().firstValue("Retry-After").orElse("60"));
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return new Result("UNKNOWN", "전송 중단: Slack 수신 여부 확인 필요", 0);
        } catch (Exception e) {
            // 예외에는 비밀 URL이 포함될 수 있다. 응답 유실 시 중복 발송을 피하려고 자동 재전송하지 않는다.
            return new Result("UNKNOWN", "전송 결과 확인 불가: Slack 수신 여부 확인 필요", 0);
        }
    }

    static Result classify(int code, String body, String retryAfter) {
        if (code == 200 && "ok".equals(body.trim())) return new Result("SENT", null, 0);
        if (code == 429) {
            int delay = 60;
            try { delay = Math.max(1, Math.min(86400, Integer.parseInt(retryAfter))); }
            catch (NumberFormatException ignored) { }
            return new Result("QUEUED", "Slack 요청 제한으로 발송 재시도 대기", delay);
        }
        if (code >= 400 && code < 500) return new Result("FAILED", "Slack 발송 거부 (HTTP " + code + ")", 0);
        return new Result("UNKNOWN", "Slack 수신 여부 확인 필요 (HTTP " + code + ")", 0);
    }
}

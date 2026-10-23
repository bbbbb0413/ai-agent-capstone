package com.opslens.backend.task;

import com.opslens.backend.config.OpsLensProperties;
import java.util.UUID;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

/** agent 서버의 내부 API. 실행 요청(HTTP 모드)과 승인 후 재개(항상 HTTP)에 쓴다. */
@Component
public class AgentClient {

    public record ResumeRequest(String decision, String comment, String approver, String approvalId) {
        public ResumeRequest(String decision, String comment, String approver) {
            this(decision, comment, approver, null);
        }
    }

    private final RestClient http;

    public AgentClient(RestClient.Builder builder, OpsLensProperties props) {
        this.http = builder.baseUrl(props.agentUrl())
                .defaultHeader("Authorization", "Bearer " + props.internalTokens().backendAgent())
                .build();
    }

    public void run(TaskMessage message) {
        http.post().uri("/agent/tasks/{id}/run", message.taskId())
                .contentType(MediaType.APPLICATION_JSON)
                .header("X-Trace-Id", message.traceId())
                .body(message)
                .retrieve().toBodilessEntity();
    }

    public void resume(UUID taskId, ResumeRequest request) {
        http.post().uri("/agent/tasks/{id}/resume", taskId)
                .contentType(MediaType.APPLICATION_JSON)
                .body(request)
                .retrieve().toBodilessEntity();
    }
}

package com.opslens.backend.tenant;

import com.opslens.backend.config.OpsLensProperties;
import java.util.Map;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

/** 고객사 서비스(demo-app)의 관리 API를 호출해 조치를 실행한다. 조치 허용 여부는 호출 전에 확인한다. */
@Component
public class DemoAppClient {

    private final RestClient http;

    public DemoAppClient(RestClient.Builder builder, OpsLensProperties props) {
        this.http = builder.defaultHeader("Authorization", "Bearer " + props.serviceToken()).build();
    }

    public Map<?, ?> execute(Tenant tenant, String action, Map<String, Object> params) {
        String base = tenant.appUrl() + "/admin";
        return switch (action) {
            case "adjust_pool" -> http.post().uri(base + "/pool").contentType(MediaType.APPLICATION_JSON)
                    .body(Map.of("size", params.getOrDefault("size", 20))).retrieve().body(Map.class);
            case "restart" -> http.post().uri(base + "/restart").retrieve().body(Map.class);
            case "clear_fault" -> http.delete().uri(base + "/faults").retrieve().body(Map.class);
            default -> throw new IllegalArgumentException("unsupported action " + action);
        };
    }
}

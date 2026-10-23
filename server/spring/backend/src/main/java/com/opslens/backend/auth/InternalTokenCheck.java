package com.opslens.backend.auth;

import com.opslens.backend.config.OpsLensProperties;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;
import org.springframework.stereotype.Component;

/**
 * 기동할 때 내부 호출 토큰을 확인한다. 비어 있거나 두 호출이 같은 값을 쓰면 기동하지 않는다.
 * 같은 값을 쓰면 한 토큰이 샐 때 다른 호출까지 열린다. 오류 메시지에는 값이 아니라 변수 이름만 남긴다.
 */
@Component
public class InternalTokenCheck {

    public InternalTokenCheck(OpsLensProperties props) {
        check(props);
    }

    static void check(OpsLensProperties props) {
        OpsLensProperties.InternalTokens t = props.internalTokens();
        Map<String, String> tokens = new LinkedHashMap<>();
        tokens.put("BACKEND_AGENT_TOKEN", t == null ? null : t.backendAgent());
        tokens.put("AGENT_BACKEND_TOKEN", t == null ? null : t.agentBackend());
        tokens.put("MCP_BACKEND_TOKEN", t == null ? null : t.mcpBackend());
        tokens.put("INDEXER_BACKEND_TOKEN", t == null ? null : t.indexerBackend());
        tokens.put("GITOPS_WEBHOOK_TOKEN", t == null ? null : t.gitopsWebhook());
        tokens.put("ALERT_WEBHOOK_TOKEN", props.alertWebhookToken());
        List<String> missing = tokens.entrySet().stream().filter(e -> e.getValue() == null || e.getValue().isBlank())
                .map(Map.Entry::getKey).toList();
        if (!missing.isEmpty()) {
            throw new IllegalStateException("내부 호출 토큰이 비어 있다: " + missing);
        }
        Map<String, List<String>> byValue = tokens.entrySet().stream().collect(Collectors.groupingBy(
                Map.Entry::getValue, LinkedHashMap::new, Collectors.mapping(Map.Entry::getKey, Collectors.toList())));
        List<List<String>> shared = byValue.values().stream().filter(names -> names.size() > 1).toList();
        if (!shared.isEmpty()) {
            throw new IllegalStateException("내부 호출 토큰은 서로 달라야 한다. 같은 값: " + shared);
        }
    }
}

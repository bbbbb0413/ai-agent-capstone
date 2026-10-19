package com.opslens.backend.task;

import java.io.IOException;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

/**
 * agent가 보낸 진행 이벤트를 화면의 SSE 연결로 중계한다 (Publish-Subscribe).
 * agent는 어떤 화면 영역이 이벤트를 받는지 모른다.
 * TODO: backend를 여러 대로 늘리면 인스턴스 간 중계(Redis pub/sub 등)가 필요하다
 */
@Component
public class TaskEventHub {

    /** 화면이 받는 이벤트 (기획안 5.3) */
    public static final Set<String> EVENT_TYPES =
            Set.of("TOKEN", "TOOL_START", "TOOL_END", "APPROVAL_REQUIRED", "CHANGE_PENDING", "DONE", "FAILED");
    private static final Set<String> TERMINAL = Set.of("DONE", "FAILED");

    private final Map<UUID, List<SseEmitter>> emitters = new ConcurrentHashMap<>();

    public SseEmitter subscribe(UUID taskId) {
        SseEmitter emitter = new SseEmitter(0L);
        emitters.computeIfAbsent(taskId, id -> new CopyOnWriteArrayList<>()).add(emitter);
        Runnable remove = () -> remove(taskId, emitter);
        emitter.onCompletion(remove);
        emitter.onTimeout(remove);
        emitter.onError(e -> remove.run());
        return emitter;
    }

    public void publish(UUID taskId, String type, Object data) {
        for (SseEmitter emitter : emitters.getOrDefault(taskId, List.of())) {
            try {
                emitter.send(SseEmitter.event().name(type).data(data));
                if (TERMINAL.contains(type)) {
                    emitter.complete();
                }
            } catch (IOException e) {
                remove(taskId, emitter);
            }
        }
    }

    private void remove(UUID taskId, SseEmitter emitter) {
        emitters.computeIfPresent(taskId, (id, list) -> {
            list.remove(emitter);
            return list.isEmpty() ? null : list;
        });
    }
}

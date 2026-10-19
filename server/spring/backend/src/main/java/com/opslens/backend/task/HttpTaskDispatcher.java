package com.opslens.backend.task;

import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

@Component
@ConditionalOnProperty(name = "opslens.dispatch-mode", havingValue = "http", matchIfMissing = true)
public class HttpTaskDispatcher implements TaskDispatcher {

    private final AgentClient agent;

    public HttpTaskDispatcher(AgentClient agent) {
        this.agent = agent;
    }

    @Override
    public void dispatch(TaskMessage message) {
        agent.run(message);
    }
}

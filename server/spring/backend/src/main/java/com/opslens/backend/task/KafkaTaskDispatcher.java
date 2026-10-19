package com.opslens.backend.task;

import static java.nio.charset.StandardCharsets.UTF_8;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.kafka.clients.admin.NewTopic;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.kafka.config.TopicBuilder;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Component;

/**
 * Kafka에는 우선순위 기능이 없어서 SLA 등급별 토픽으로 나눈다. agent consumer가 Gold 토픽을 먼저 비운다.
 * 메시지 키는 tenant_id라서 같은 고객사의 작업은 같은 파티션에서 순서대로 처리된다.
 */
@Component
@ConditionalOnProperty(name = "opslens.dispatch-mode", havingValue = "kafka")
public class KafkaTaskDispatcher implements TaskDispatcher {

    private final KafkaTemplate<String, String> kafka;
    private final ObjectMapper json;

    public KafkaTaskDispatcher(KafkaTemplate<String, String> kafka, ObjectMapper json) {
        this.kafka = kafka;
        this.json = json;
    }

    @Override
    public void dispatch(TaskMessage message) {
        String topic = "tasks." + message.slaTier().toLowerCase();
        var record = new ProducerRecord<>(topic, message.tenantId(), toJson(message));
        record.headers().add("task_id", message.taskId().toString().getBytes(UTF_8));
        record.headers().add("trace_id", message.traceId().getBytes(UTF_8));
        kafka.send(record);
    }

    private String toJson(TaskMessage message) {
        try {
            return json.writeValueAsString(message);
        } catch (JsonProcessingException e) {
            throw new IllegalArgumentException(e);
        }
    }

    @Configuration
    @ConditionalOnProperty(name = "opslens.dispatch-mode", havingValue = "kafka")
    static class Topics {

        @Bean
        NewTopic goldTopic() {
            return TopicBuilder.name("tasks.gold").partitions(3).replicas(1).build();
        }

        @Bean
        NewTopic silverTopic() {
            return TopicBuilder.name("tasks.silver").partitions(3).replicas(1).build();
        }

        @Bean
        NewTopic retryTopic() {
            return TopicBuilder.name("tasks.retry").partitions(3).replicas(1).build();
        }

        @Bean
        NewTopic deadLetterTopic() {
            return TopicBuilder.name("tasks.dlt").partitions(1).replicas(1).build();
        }
    }
}

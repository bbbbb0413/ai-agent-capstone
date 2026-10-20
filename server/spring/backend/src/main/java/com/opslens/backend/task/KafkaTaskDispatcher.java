package com.opslens.backend.task;

import static java.nio.charset.StandardCharsets.UTF_8;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import org.apache.kafka.clients.admin.NewTopic;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.kafka.config.TopicBuilder;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Component;

/**
 * Kafka에는 우선순위 기능이 없어서 SLA 등급별 토픽으로 나눈다. agent consumer가 Gold 토픽을 먼저 비운다.
 * 메시지 키는 tenant_id라서 같은 고객사의 작업은 같은 파티션에서 순서대로 처리된다.
 * 브로커가 받았다는 응답(acks=all)까지 기다린다. 기다리지 않으면 전송 실패를 알 수 없어 작업이 QUEUED로 남는다.
 */
@Component
@ConditionalOnProperty(name = "opslens.dispatch-mode", havingValue = "kafka", matchIfMissing = true)
public class KafkaTaskDispatcher implements TaskDispatcher {

    /** 브로커 응답을 기다리는 시간. producer delivery.timeout.ms(application.yml)와 같다. */
    private static final long SEND_TIMEOUT_SECONDS = 5;

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
        try {
            kafka.send(record).get(SEND_TIMEOUT_SECONDS, TimeUnit.SECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("Kafka 발행 중단", e);
        } catch (ExecutionException | TimeoutException e) {
            throw new IllegalStateException("Kafka 발행 실패: " + topic, e);
        }
    }

    private String toJson(TaskMessage message) {
        try {
            return json.writeValueAsString(message);
        } catch (JsonProcessingException e) {
            throw new IllegalArgumentException(e);
        }
    }

    @Configuration
    @ConditionalOnProperty(name = "opslens.dispatch-mode", havingValue = "kafka", matchIfMissing = true)
    static class Topics {

        /** 파티션 수는 동시에 처리할 수 있는 고객사 묶음 수의 상한이다. 기본 3, 환경변수 KAFKA_PARTITIONS로 바꾼다. */
        private final int partitions;

        Topics(@Value("${opslens.kafka.partitions:3}") int partitions) {
            this.partitions = partitions;
        }

        @Bean
        NewTopic goldTopic() {
            return TopicBuilder.name("tasks.gold").partitions(partitions).replicas(1).build();
        }

        @Bean
        NewTopic silverTopic() {
            return TopicBuilder.name("tasks.silver").partitions(partitions).replicas(1).build();
        }

        /** 재시도도 등급별로 나눈다. 하나로 두면 Silver 재시도가 Gold 소비 루프에 섞인다 (SPEC-kafka-scale 4.3(2)). */
        @Bean
        NewTopic goldRetryTopic() {
            return TopicBuilder.name("tasks.retry.gold").partitions(partitions).replicas(1).build();
        }

        @Bean
        NewTopic silverRetryTopic() {
            return TopicBuilder.name("tasks.retry.silver").partitions(partitions).replicas(1).build();
        }

        @Bean
        NewTopic deadLetterTopic() {
            return TopicBuilder.name("tasks.dlt").partitions(1).replicas(1).build();
        }
    }
}

package com.opslens.backend.document;

import static java.nio.charset.StandardCharsets.UTF_8;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.UUID;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import org.apache.kafka.clients.admin.NewTopic;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.kafka.config.TopicBuilder;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * 색인 요청을 Kafka `documents.index`로 보낸다 (SPEC-document-store 2장). 키는 문서 ID라 같은 문서의 버전이 순서대로 간다.
 * - 저장 직후 kick()으로 바로 보내고, 실패하면 2^시도 초 뒤에 다시 보낸다(TaskOutboxRelay와 같다)
 * - 5분마다 오래 대기 중인 버전과 다시 시도할 실패를 다시 넣는다. 메시지를 놓쳤을 때 대비다
 * - 끝내 보내지 못해도 버전은 PENDING으로 남는다. 작업과 달리 문서는 실패로 끝내지 않고 점검이 다시 넣는다
 * Kafka 없이(DISPATCH_MODE=http) 돌 때는 이 빈이 없고, indexer가 내부 API로 대기 문서를 가져간다.
 */
@Component
@ConditionalOnProperty(name = "opslens.dispatch-mode", havingValue = "kafka", matchIfMissing = true)
public class DocumentIndexRelay implements DocumentService.IndexRequests {

    public static final String TOPIC = "documents.index";
    static final int MAX_ATTEMPTS = 5;
    /** 업로드 뒤 이 시간이 지나도 PENDING이면 요청을 다시 넣는다 */
    static final int STALE_PENDING_MINUTES = 2;
    private static final long SEND_TIMEOUT_SECONDS = 5;
    private static final Logger log = LoggerFactory.getLogger(DocumentIndexRelay.class);

    private final DocumentOutbox outbox;
    private final DocumentRepository documents;
    private final KafkaTemplate<String, String> kafka;
    private final ObjectMapper json;
    private final ExecutorService sender = Executors.newVirtualThreadPerTaskExecutor();

    public DocumentIndexRelay(DocumentOutbox outbox, DocumentRepository documents, KafkaTemplate<String, String> kafka,
            ObjectMapper json) {
        this.outbox = outbox;
        this.documents = documents;
        this.kafka = kafka;
        this.json = json;
    }

    @Override
    public void request(DocumentOutbox.Message message) {
        outbox.enqueue(message);
    }

    /** 트랜잭션이 커밋된 뒤에 부른다 */
    @Override
    public void kick(UUID documentId) {
        sender.execute(() -> {
            try {
                outbox.claim(documentId).ifPresent(this::send);
            } catch (RuntimeException e) {
                log.warn("document {} immediate index request skipped, the scheduled relay will retry", documentId, e);
            }
        });
    }

    @Scheduled(fixedDelay = 1000, initialDelay = 2000)
    void relayPending() {
        outbox.claim(20).forEach(this::send);
    }

    /** 놓친 요청과 다시 시도할 실패를 다시 넣는다 */
    @Scheduled(fixedDelay = 5 * 60 * 1000, initialDelay = 60 * 1000)
    void sweep() {
        for (DocumentRepository.Version v : documents.waiting(STALE_PENDING_MINUTES)) {
            if (!outbox.hasPending(v.documentId())) {
                outbox.enqueue(new DocumentOutbox.Message(DocumentOutbox.INDEX, v.documentId(), v.id()));
                documents.markRequested(v.id());
            }
        }
        for (DocumentRepository.Document d : documents.deletedWithChunks()) {
            if (!outbox.hasPending(d.id())) {
                outbox.enqueue(new DocumentOutbox.Message(DocumentOutbox.DELETE, d.id(), null));
            }
        }
    }

    void send(DocumentOutbox.Claimed claimed) {
        DocumentOutbox.Message message = claimed.message();
        var record = new ProducerRecord<>(TOPIC, message.documentId().toString(), toJson(message));
        record.headers().add("kind", message.kind().getBytes(UTF_8));
        try {
            kafka.send(record).get(SEND_TIMEOUT_SECONDS, TimeUnit.SECONDS);
            outbox.markSent(claimed.id());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            outbox.retryLater(claimed.id(), "interrupted", 1);
        } catch (ExecutionException | TimeoutException | RuntimeException e) {
            String error = e.getClass().getSimpleName() + ": " + e.getMessage();
            if (claimed.attempts() >= MAX_ATTEMPTS) {
                log.error("document {} index request failed {} times, the sweep will enqueue it again",
                        message.documentId(), claimed.attempts());
                outbox.markFailed(claimed.id(), error);
            } else {
                outbox.retryLater(claimed.id(), error, 1L << claimed.attempts());
            }
        }
    }

    private String toJson(DocumentOutbox.Message message) {
        try {
            return json.writeValueAsString(message);
        } catch (JsonProcessingException e) {
            throw new IllegalArgumentException(e);
        }
    }

    @Configuration
    @ConditionalOnProperty(name = "opslens.dispatch-mode", havingValue = "kafka", matchIfMissing = true)
    static class Topics {

        private final int partitions;

        Topics(@Value("${opslens.kafka.partitions:3}") int partitions) {
            this.partitions = partitions;
        }

        @Bean
        NewTopic documentIndexTopic() {
            return TopicBuilder.name(TOPIC).partitions(partitions).replicas(1).build();
        }
    }
}

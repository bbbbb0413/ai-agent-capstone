package com.opslens.backend.document;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Stream;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Component;

/**
 * 처음 데이터 (SPEC-document-store 6장). 저장소의 data/documents/{범위}/*.md 중 DB에 없는 문서만 버전 1로 올린다.
 * 이미 있으면 건드리지 않아 화면에서 올린 버전을 덮어쓰지 않는다. 화면 업로드와 같은 경로(저장소 → 버전 → 색인 요청)를 탄다.
 * 문서 종류는 파일 이름 접두어로, 제목은 첫 `# ` 줄로 정한다(시드에만 쓰는 규칙).
 */
@Component
public class DocumentSeeder {

    static final Map<String, String> PREFIX_TYPES = Map.of("runbook", "runbook", "sla", "sla");
    private static final Pattern TITLE = Pattern.compile("(?m)^# (.+)$");
    private static final Logger log = LoggerFactory.getLogger(DocumentSeeder.class);

    private final DocumentService service;
    private final DocumentStoreProperties props;

    public DocumentSeeder(DocumentService service, DocumentStoreProperties props) {
        this.service = service;
        this.props = props;
    }

    record SeedFile(String tenantId, String docId, String docType, String title, Path path) {}

    @EventListener(ApplicationReadyEvent.class)
    void seed() {
        if (props.seedDir() == null || props.seedDir().isBlank() || !Files.isDirectory(Path.of(props.seedDir()))) {
            return;
        }
        int added = 0;
        for (SeedFile file : files(Path.of(props.seedDir()))) {
            try {
                if (service.seed(file.tenantId(), file.docId(), file.docType(), file.title(),
                        file.path().getFileName().toString(), Files.readAllBytes(file.path()))) {
                    added++;
                }
            } catch (RuntimeException | IOException e) {
                // 고객사가 아직 없거나 저장소가 준비되지 않았으면 그 문서만 건너뛴다. 다음 기동 때 다시 올린다
                log.warn("seed document {}/{} skipped: {}", file.tenantId(), file.docId(), e.getMessage());
            }
        }
        if (added > 0) {
            log.info("seeded {} documents from {}", added, props.seedDir());
        }
    }

    static List<SeedFile> files(Path root) {
        try (Stream<Path> scopes = Files.list(root)) {
            return scopes.filter(Files::isDirectory).sorted().flatMap(scope -> {
                try (Stream<Path> docs = Files.list(scope)) {
                    return docs.filter(p -> p.toString().endsWith(".md")).sorted().map(p -> toSeed(scope, p))
                            .filter(java.util.Objects::nonNull).toList().stream();
                } catch (IOException e) {
                    return Stream.empty();
                }
            }).toList();
        } catch (IOException e) {
            return List.of();
        }
    }

    private static SeedFile toSeed(Path scope, Path path) {
        String docId = path.getFileName().toString().replaceFirst("\\.md$", "");
        String type = PREFIX_TYPES.get(docId.split("-", 2)[0]);
        if (type == null) {
            return null;   // 회고(postmortem-*)는 종료된 인시던트에서 만들어진다
        }
        try {
            Matcher m = TITLE.matcher(Files.readString(path, StandardCharsets.UTF_8));
            return new SeedFile(scope.getFileName().toString(), docId, type, m.find() ? m.group(1).strip() : docId, path);
        } catch (IOException e) {
            return null;
        }
    }
}

package com.opslens.backend.document;

import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.StandardCharsets;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

/**
 * 업로드 때 backend가 하는 검사 (SPEC-document-store 4.1). 파일 내용(제목 구조, 조항 번호)은 해석하지 않는다.
 * 구조 검사는 indexer가 파싱한 뒤 하고 실패 사유를 남긴다. 여기서는 형식·크기·이름만 본다.
 */
public final class DocumentFiles {

    public static final String COMMON = "_common";

    /** 형식별 최대 크기. 텍스트는 1MB, PDF·Word는 20MB */
    static final Map<String, Integer> MAX_BYTES = Map.of("md", 1 << 20, "txt", 1 << 20,
            "pdf", 20 << 20, "docx", 20 << 20);
    static final Map<String, String> CONTENT_TYPES = Map.of("md", "text/markdown; charset=utf-8",
            "txt", "text/plain; charset=utf-8", "pdf", "application/pdf",
            "docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document");
    static final Set<String> TENANT_TYPES = Set.of("runbook", "sla");
    static final Set<String> COMMON_TYPES = Set.of("runbook");

    private static final Pattern DOC_ID = Pattern.compile("[a-z0-9][a-z0-9-]{1,62}");
    private static final byte[] PDF_MAGIC = "%PDF-".getBytes(StandardCharsets.US_ASCII);
    private static final byte[] ZIP_MAGIC = {0x50, 0x4b, 0x03, 0x04};

    private DocumentFiles() {}

    public record Checked(String filename, String format, byte[] content) {}

    /** 확장자와 파일 앞부분이 맞는지 본다. 확장자만 바꾼 파일을 막는다. */
    public static Checked check(String originalFilename, byte[] content) {
        String filename = cleanName(originalFilename);
        int dot = filename.lastIndexOf('.');
        String format = dot < 0 ? "" : filename.substring(dot + 1).toLowerCase(Locale.ROOT);
        Integer max = MAX_BYTES.get(format);
        if (max == null) {
            throw unprocessable("지원하는 형식은 md, txt, pdf, docx다 (받은 파일: %s)".formatted(filename));
        }
        if (content.length == 0) {
            throw unprocessable("빈 파일은 올릴 수 없다");
        }
        if (content.length > max) {
            throw unprocessable("%s 파일은 %dMB 이하여야 한다".formatted(format, max >> 20));
        }
        boolean matches = switch (format) {
            case "pdf" -> startsWith(content, PDF_MAGIC);
            case "docx" -> startsWith(content, ZIP_MAGIC);
            default -> isUtf8Text(content);
        };
        if (!matches) {
            throw unprocessable(format.equals("md") || format.equals("txt")
                    ? "UTF-8 텍스트 파일이 아니다"
                    : "파일 내용이 %s 형식이 아니다. 확장자를 확인한다".formatted(format));
        }
        return new Checked(filename, format, content);
    }

    public static void checkType(String tenantId, String docType) {
        Set<String> allowed = COMMON.equals(tenantId) ? COMMON_TYPES : TENANT_TYPES;
        if (!allowed.contains(docType)) {
            throw unprocessable("%s 문서의 종류는 %s 중 하나다 (받은 값: %s)"
                    .formatted(COMMON.equals(tenantId) ? "공통" : "고객사", allowed, docType));
        }
    }

    public static void checkDocId(String docId) {
        if (docId == null || !DOC_ID.matcher(docId).matches()) {
            throw unprocessable("문서 ID는 영문 소문자·숫자·-로 2~63자다 (예: runbook-order-api)");
        }
    }

    public static String checkTitle(String title) {
        String trimmed = title == null ? "" : title.strip();
        if (trimmed.isEmpty() || trimmed.length() > 200 || trimmed.contains("\n")) {
            throw unprocessable("제목은 한 줄, 200자 이내로 적는다");
        }
        return trimmed;
    }

    static String cleanName(String name) {
        String base = (name == null ? "document" : name).replace('\\', '/').replaceAll("^.*/", "")
                .replaceAll("\\p{Cntrl}", "_");
        return base.length() > 160 ? base.substring(base.length() - 160) : base;
    }

    private static boolean startsWith(byte[] content, byte[] magic) {
        if (content.length < magic.length) {
            return false;
        }
        for (int i = 0; i < magic.length; i++) {
            if (content[i] != magic[i]) {
                return false;
            }
        }
        return true;
    }

    private static boolean isUtf8Text(byte[] content) {
        try {
            String text = StandardCharsets.UTF_8.newDecoder().decode(ByteBuffer.wrap(content)).toString();
            return !text.isBlank() && text.chars().noneMatch(c -> c < 32 && c != '\t' && c != '\n' && c != '\r');
        } catch (CharacterCodingException e) {
            return false;
        }
    }

    static ResponseStatusException unprocessable(String message) {
        return new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, message);
    }
}

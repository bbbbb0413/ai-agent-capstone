package com.opslens.backend.attachment;

import java.io.ByteArrayInputStream;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.util.*;
import javax.imageio.ImageIO;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;

@Repository
public class TaskAttachments {
    public static final int MAX_FILES = 3, MAX_FILE = 4 * 1024 * 1024, MAX_TOTAL = 8 * 1024 * 1024;
    public record Prepared(String filename, String mediaType, byte[] content) {}
    public record Info(UUID id, String filename, String mediaType, int size) {}
    public record Content(UUID id, String filename, String mediaType, byte[] content) {}
    private final JdbcClient jdbc;
    public TaskAttachments(JdbcClient jdbc) { this.jdbc = jdbc; }

    public static List<Prepared> validate(List<MultipartFile> files) {
        if (files == null || files.isEmpty() || files.size() > MAX_FILES) bad("파일은 1~3개 첨부하세요");
        long total = files.stream().mapToLong(MultipartFile::getSize).sum();
        if (total > MAX_TOTAL) bad("첨부 파일 합계는 8MB 이하여야 합니다");
        var result = new ArrayList<Prepared>();
        for (MultipartFile file : files) {
            if (file.isEmpty() || file.getSize() > MAX_FILE) bad("빈 파일은 첨부할 수 없으며 파일당 최대 4MB입니다");
            String name = Optional.ofNullable(file.getOriginalFilename()).orElse("attachment")
                .replace('\\', '/').replaceAll("^.*/", "").replaceAll("[\\p{Cntrl}]", "_");
            if (name.length() > 120) name = name.substring(name.length() - 120);
            String lower = name.toLowerCase(Locale.ROOT);
            try {
                byte[] bytes = file.getBytes();
                String type;
                if (lower.matches(".*\\.(png|jpe?g)$")) {
                    try (var input = ImageIO.createImageInputStream(new ByteArrayInputStream(bytes))) {
                        var readers = ImageIO.getImageReaders(input);
                        if (!readers.hasNext()) bad("읽을 수 없는 이미지입니다");
                        var reader = readers.next();
                        try {
                            String format = reader.getFormatName().toLowerCase(Locale.ROOT);
                            if (!Set.of("png", "jpeg").contains(format)) bad("PNG/JPEG 이미지만 지원합니다");
                            reader.setInput(input, true, true);
                            int w = reader.getWidth(0), h = reader.getHeight(0);
                            if (w < 1 || h < 1 || w > 8192 || h > 8192 || (long)w*h > 16000000)
                                bad("이미지는 최대 1600만 픽셀, 한 변 8192픽셀까지 지원합니다");
                            if (reader.read(0) == null) bad("손상된 이미지입니다");
                            type = format.equals("png") ? "image/png" : "image/jpeg";
                        } finally { reader.dispose(); }
                    }
                } else if (lower.matches(".*\\.(txt|log|json|jsonl|ndjson|csv)$")) {
                    if (bytes.length > 512 * 1024) bad("텍스트 로그는 파일당 512KB까지 지원합니다");
                    String text = StandardCharsets.UTF_8.newDecoder().decode(ByteBuffer.wrap(bytes)).toString();
                    if (text.isBlank() || text.chars().anyMatch(c -> c < 32 && c != 9 && c != 10 && c != 13))
                        bad("UTF-8 텍스트 로그를 첨부하세요");
                    type = "text/plain";
                } else { bad("PNG/JPEG 또는 TXT/LOG/JSON/JSONL/NDJSON/CSV 파일을 첨부하세요"); return List.of(); }
                result.add(new Prepared(name, type, bytes));
            } catch (ResponseStatusException e) { throw e; }
            catch (Exception e) { bad("파일을 읽을 수 없습니다. 이미지 또는 UTF-8 텍스트인지 확인하세요"); }
        }
        return result;
    }
    public void save(UUID task, List<Prepared> files) {
        for (Prepared file : files) jdbc.sql("INSERT INTO task_attachments(id,task_id,filename,media_type,content) VALUES(?,?,?,?,?)")
            .params(UUID.randomUUID(),task,file.filename(),file.mediaType(),file.content()).update();
    }
    public List<Info> list(UUID task) {
        return jdbc.sql("SELECT id,filename,media_type,octet_length(content) FROM task_attachments WHERE task_id=? ORDER BY created_at,id")
            .param(task).query((r,n)->new Info(r.getObject(1,UUID.class),r.getString(2),r.getString(3),r.getInt(4))).list();
    }
    public List<Content> contents(UUID task) {
        return jdbc.sql("SELECT id,filename,media_type,content FROM task_attachments WHERE task_id=? ORDER BY created_at,id")
            .param(task).query((r,n)->new Content(r.getObject(1,UUID.class),r.getString(2),r.getString(3),r.getBytes(4))).list();
    }
    public Content get(UUID task, UUID id) {
        return contents(task).stream().filter(f->f.id().equals(id)).findFirst()
            .orElseThrow(()->new ResponseStatusException(HttpStatus.NOT_FOUND,"첨부 파일을 찾을 수 없습니다"));
    }
    private static void bad(String message) { throw new ResponseStatusException(HttpStatus.BAD_REQUEST,message); }
}

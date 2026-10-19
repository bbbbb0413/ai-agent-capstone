package com.opslens.backend.change;

import static java.nio.charset.StandardCharsets.UTF_8;

import com.fasterxml.jackson.databind.JsonNode;
import com.opslens.backend.config.OpsLensProperties;
import com.opslens.backend.tenant.Tenant;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.function.UnaryOperator;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientResponseException;
import org.springframework.web.server.ResponseStatusException;

/**
 * GITOPS 고객사의 설정 저장소(GitHub). 원하는 상태를 바꾸는 조치를 브랜치·커밋·PR로 만든다.
 * main에 직접 커밋하지 않고, 고객사 경로(tenant.gitopsPath) 밖의 파일은 바꾸지 않는다.
 * 병합은 사람이 한다. 병합 후 ArgoCD 동기화가 끝나면 GitOpsSyncController가 알림을 받는다.
 */
@Component
public class GitOpsRepository {

    public record PullRequest(int number, String url, String branch, List<String> files) {}

    public record Commit(String sha, String message, String author, String date, String patch) {}

    private static final int PATCH_CHARS = 600;

    private final RestClient http;
    private final OpsLensProperties.Gitops props;

    public GitOpsRepository(RestClient.Builder builder, OpsLensProperties props) {
        this.props = props.gitops();
        // 저장소 이름과 파일 경로에는 /가 들어간다. URI 변수로 넘기면 %2F로 인코딩되므로 경로에 직접 붙인다
        RestClient.Builder b = builder.clone().baseUrl(this.props.apiUrl() + "/repos/" + this.props.repo())
                .defaultHeader("Accept", "application/vnd.github+json")
                .defaultHeader("X-GitHub-Api-Version", "2022-11-28");
        if (this.props.token() != null && !this.props.token().isBlank()) {
            b.defaultHeader("Authorization", "Bearer " + this.props.token());
        }
        this.http = b.build();
    }

    /** kustomization.yaml의 replicas 항목에서 서비스의 count를 바꾸는 PR. */
    public PullRequest scale(Tenant tenant, String service, int replicas, String reason) {
        String file = tenant.gitopsPath() + "/kustomization.yaml";
        Pattern count = Pattern.compile("(- name: " + Pattern.quote(service) + "\\s*\\n\\s*count: )(\\d+)");
        return openPullRequest(tenant, "scale-" + service,
                "Scale %s to %d replicas".formatted(service, replicas), reason,
                Map.of(file, content -> {
                    Matcher m = count.matcher(content);
                    if (!m.find()) {
                        throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY,
                                "%s에 %s의 replicas 항목이 없다".formatted(file, service));
                    }
                    return m.replaceFirst("$1" + replicas);
                }));
    }

    /**
     * 장애 시작 전 마지막 변경을 되돌리는 PR. 그 커밋이 바꾼 파일을 커밋 직전 내용으로 복원한다.
     * "가장 최근 커밋"이 아니라 장애 시작 시각 기준으로 고른다. 그래야 롤백을 두 번 해도 수정 커밋을 되돌리지 않는다.
     * 이미 복원된 상태면 PR을 만들지 않고 거부한다.
     */
    public PullRequest rollback(Tenant tenant, String service, String reason, OffsetDateTime incidentStartedAt) {
        JsonNode culprit = commitNodes(tenant, 1, incidentStartedAt).stream().findFirst().orElseThrow(() ->
                new ResponseStatusException(HttpStatus.CONFLICT,
                        "%s에 장애 시작 전 변경 이력이 없다".formatted(tenant.gitopsPath())));
        String sha = culprit.path("sha").asText();
        JsonNode detail = get("/commits/{sha}", sha);
        String parent = detail.path("parents").path(0).path("sha").asText();
        Map<String, UnaryOperator<String>> edits = new LinkedHashMap<>();
        boolean restored = true;
        for (JsonNode f : detail.path("files")) {
            String path = f.path("filename").asText();
            if (path.startsWith(tenant.gitopsPath() + "/")) {
                String before = readFile(path, parent);
                restored &= before.equals(readFile(path, props.baseBranch()));
                edits.put(path, current -> before);
            }
        }
        if (restored) {
            throw new ResponseStatusException(HttpStatus.CONFLICT,
                    "장애 시작 전 변경(%s)은 이미 되돌려져 있다. 롤백을 다시 하지 말고 다른 원인을 조사하거나 에스컬레이션한다"
                            .formatted(sha.substring(0, 7)));
        }
        String subject = detail.path("commit").path("message").asText().lines().findFirst().orElse("");
        return openPullRequest(tenant, "rollback-" + service,
                "Revert \"%s\" (%s)".formatted(subject, sha.substring(0, 7)), reason, edits);
    }

    /** 고객사 경로의 최근 커밋 (최신순). 커밋마다 변경 내용의 앞부분을 붙인다. */
    public List<Commit> recentCommits(Tenant tenant, int limit) {
        List<Commit> commits = new ArrayList<>();
        for (JsonNode c : commitNodes(tenant, limit, null)) {
            JsonNode detail = get("/commits/{sha}", c.path("sha").asText());
            StringBuilder patch = new StringBuilder();
            for (JsonNode f : detail.path("files")) {
                if (!f.path("filename").asText().startsWith(tenant.gitopsPath() + "/")) {
                    continue;  // 같은 커밋의 다른 경로(앱 코드 등)는 이 고객사 변경이 아니다
                }
                patch.append(f.path("filename").asText()).append('\n').append(f.path("patch").asText()).append('\n');
            }
            String text = patch.length() > PATCH_CHARS ? patch.substring(0, PATCH_CHARS) + "…" : patch.toString();
            JsonNode commit = c.path("commit");
            commits.add(new Commit(c.path("sha").asText().substring(0, 7), commit.path("message").asText(),
                    commit.path("author").path("name").asText(), commit.path("author").path("date").asText(), text));
        }
        return commits;
    }

    public JsonNode pullRequest(int number) {
        return get("/pulls/{n}", number);
    }

    // ── PR 만들기 ──────────────────────────────────────────────

    private PullRequest openPullRequest(Tenant tenant, String branchPrefix, String title, String reason,
            Map<String, UnaryOperator<String>> edits) {
        requireToken();
        for (String path : edits.keySet()) {
            if (!path.startsWith(tenant.gitopsPath() + "/")) {
                throw new ResponseStatusException(HttpStatus.FORBIDDEN,
                        "%s는 %s의 설정 경로(%s) 밖이다".formatted(path, tenant.name(), tenant.gitopsPath()));
            }
        }
        String base = get("/git/ref/heads/{b}", props.baseBranch()).path("object").path("sha").asText();
        String branch = "opslens/%s/%s-%d".formatted(tenant.id(), branchPrefix, System.currentTimeMillis() / 1000);
        send(http.post().uri("/git/refs")
                .body(Map.of("ref", "refs/heads/" + branch, "sha", base)));

        List<String> changed = new ArrayList<>();
        for (var edit : edits.entrySet()) {
            JsonNode file = get("/contents/" + edit.getKey() + "?ref={ref}", branch);
            String current = decode(file.path("content").asText());
            String updated = edit.getValue().apply(current);
            if (updated.equals(current)) {
                continue;
            }
            send(http.put().uri("/contents/" + edit.getKey()).body(Map.of(
                    "message", title, "branch", branch, "sha", file.path("sha").asText(),
                    "content", Base64.getEncoder().encodeToString(updated.getBytes(UTF_8)))));
            changed.add(edit.getKey());
        }
        if (changed.isEmpty()) {
            send(http.delete().uri("/git/refs/heads/" + branch));
            throw new ResponseStatusException(HttpStatus.CONFLICT, "이미 원하는 상태와 같아서 바꿀 내용이 없다");
        }
        JsonNode pr = send(http.post().uri("/pulls").body(Map.of(
                "title", "[OpsLens] " + title, "head", branch, "base", props.baseBranch(),
                "body", "OpsLens가 승인된 조치로 만든 PR이다. 병합하면 ArgoCD가 클러스터에 반영한다.\n\n" + reason)));
        return new PullRequest(pr.path("number").asInt(), pr.path("html_url").asText(), branch, changed);
    }

    // ── GitHub API ─────────────────────────────────────────────

    /** 고객사 경로의 커밋 (최신순). until을 주면 그 시각 이전 커밋만. */
    private List<JsonNode> commitNodes(Tenant tenant, int limit, OffsetDateTime until) {
        List<JsonNode> list = new ArrayList<>();
        JsonNode commits = until == null
                ? get("/commits?sha={b}&path={path}&per_page={n}", props.baseBranch(), tenant.gitopsPath(), limit)
                : get("/commits?sha={b}&path={path}&per_page={n}&until={until}", props.baseBranch(),
                        tenant.gitopsPath(), limit, until.toInstant().toString());
        commits.forEach(list::add);
        return list;
    }

    private String readFile(String path, String ref) {
        return decode(get("/contents/" + path + "?ref={ref}", ref).path("content").asText());
    }

    private JsonNode get(String uri, Object... vars) {
        return send(http.get().uri(uri, vars));
    }

    private JsonNode send(RestClient.RequestHeadersSpec<?> request) {
        try {
            return request.retrieve().body(JsonNode.class);
        } catch (RestClientResponseException e) {
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY,
                    "설정 저장소(GitHub) %d: %s".formatted(e.getStatusCode().value(), e.getStatusText()));
        }
    }

    private void requireToken() {
        if (props.token() == null || props.token().isBlank()) {
            throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE,
                    "설정 저장소 토큰(GITHUB_TOKEN)이 없어 PR을 만들 수 없다. 담당자에게 에스컬레이션한다");
        }
    }

    private static String decode(String base64) {
        return new String(Base64.getMimeDecoder().decode(base64), UTF_8);
    }
}

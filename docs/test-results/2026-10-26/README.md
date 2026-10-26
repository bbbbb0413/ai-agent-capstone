# E2E 검증 결과 — 누적 18개 중 17개 통과·1개 실패

기준일: 2026-10-26. 테스트 대상 소스: [`264e4fb`](https://github.com/bbbbb0413/ai-agent-capstone/tree/264e4fbe7797039b34488c3adb5efe06d0d47fdc).

**각 시나리오의 마지막 확인 근거를 합산한 누적 결과다. 현재 코드에서 18개 전체를 한 번에 재실행한 결과는 아니다.**

- 2026-10-23: 기존 14개 통과 문서 기록.
- 2026-10-26: `no-issue`, `question`, `documents` 직접 실행, 3개 통과·세부 검사 25/25.
- 2026-10-26: 복합 장애 3개 직접 실행, **2개 통과·1개 실패**, 세부 검사 **25/26**.
- 중복 시나리오는 한 번만 센다. `question`·`no-issue`는 과거 14개와 겹친다. **누적 17개 통과·1개 실패**, 이번 직접 실행은 **6개 중 5개 통과·1개 실패**다.
- 과거 기록 12개와 이번 통과 5개를 합산한 수치다. 과거 12개가 현재 환경에서도 모두 통과한다고 보장하지 않는다.

## 18개 시나리오별 마지막 확인 결과

| 키 | 시나리오 | 판정 | 확인일 | 근거 종류 | 근거 |
|---|---|---|---|---|---|
| `isolation` | 고객사 격리 | **통과** | 2026-10-23 | 과거 문서 기록 | [기존 검증 기록](../../specs/SPEC-limitations-fixes.md) |
| `guard` | Backend 정책 방어선 | **통과** | 2026-10-23 | 과거 문서 기록 | [기존 검증 기록](../../specs/SPEC-limitations-fixes.md) |
| `question` | 문서 질문 | **통과** | 2026-10-26 | 직접 실행 | [실행 로그](common_scenarios.log) |
| `no-issue` | 이상 없음 | **통과** | 2026-10-26 | 직접 실행 | [실행 로그](common_scenarios.log) |
| `documents` | 문서 업로드·색인·검색·복원 | **통과** | 2026-10-26 | 직접 실행 | [실행 로그](common_scenarios.log) |
| `a-pool` | A사 잘못된 배포·롤백 | **통과** | 2026-10-23 | 과거 문서 기록 | [기존 검증 기록](../../specs/SPEC-limitations-fixes.md) |
| `a-hang` | A사 파드 멈춤·재시작 | **통과** | 2026-10-23 | 과거 문서 기록 | [기존 검증 기록](../../specs/SPEC-limitations-fixes.md) |
| `a-surge` | A사 트래픽 급증·확장 | **통과** | 2026-10-23 | 과거 문서 기록 | [기존 검증 기록](../../specs/SPEC-limitations-fixes.md) |
| `a-surge-max` | A사 안전 상한 초과·이관 | **통과** | 2026-10-23 | 과거 문서 기록 | [기존 검증 기록](../../specs/SPEC-limitations-fixes.md) |
| `a-latency` | A사 외부 결제 지연 | **통과** | 2026-10-23 | 과거 문서 기록 | [기존 검증 기록](../../specs/SPEC-limitations-fixes.md) |
| `a-dbslow` | A사 DB 지연 | **통과** | 2026-10-23 | 과거 문서 기록 | [기존 검증 기록](../../specs/SPEC-limitations-fixes.md) |
| `b-hang` | B사 파드 멈춤·재시작 | **통과** | 2026-10-23 | 과거 문서 기록 | [기존 검증 기록](../../specs/SPEC-limitations-fixes.md) |
| `b-latency` | B사 외부 결제 지연 | **통과** | 2026-10-23 | 과거 문서 기록 | [기존 검증 기록](../../specs/SPEC-limitations-fixes.md) |
| `b-pool` | B사 롤백 계약 제한 | **통과** | 2026-10-23 | 과거 문서 기록 | [기존 검증 기록](../../specs/SPEC-limitations-fixes.md) |
| `b-surge` | B사 확장 계약 제한 | **통과** | 2026-10-23 | 과거 문서 기록 | [기존 검증 기록](../../specs/SPEC-limitations-fixes.md) |
| `b-hang-latency` | B사 파드 멈춤 + 결제 지연 | **실패** | 2026-10-26 | 직접 실행 | [b-hang-latency.md](b-hang-latency.md) |
| `a-dbslow-backlog` | A사 DB 지연 + 트래픽 급증 | **통과** | 2026-10-26 | 직접 실행 | [a-dbslow-backlog.md](a-dbslow-backlog.md) |
| `b-pool-dbslow` | B사 잘못된 배포 + DB 지연 | **통과** | 2026-10-26 | 직접 실행 | [b-pool-dbslow.md](b-pool-dbslow.md) |

기존 14개 목록과 날짜는 [SPEC-limitations-fixes.md의 2026-10-23 E2E 기록](../../specs/SPEC-limitations-fixes.md)에 따른다. 과거 실행 원시 로그를 이번에 새로 수집한 것은 아니다.

## 복합 장애 실행 결과

| 시나리오 | 판정 | 검사 | 시간 | 확인한 동작 |
|---|---|---:|---:|---|
| `a-dbslow-backlog` | 통과 | 8/8 | 95초 | DB 지연 비율 0.65, RB-STD-05 선택, DBA 이관 |
| `b-pool-dbslow` | 통과 | 9/9 | 103초 | RB-STD-01 선택, 롤백 CONTRACT 제한 이관, 앱 조치 0건 |
| `b-hang-latency` | 실패 | 8/9 | 175초 | RB-STD-02 재시작 → RB-STD-04 재진단은 정상, 공지 도구 호출 실패 |

사전 점검 11개는 별도 통과했다. 이를 시나리오 수나 위 25/26에 합산하지 않았다. A사 실행은 `dependency_share=0.65`라 DBA 이관 분기였다. HPA 최대치 확대 PR 분기를 이번 실행에서 검증한 것은 아니다.

## 실패 원인 — 공지 도구 인자 검증

`b-hang-latency`의 공지 승인 요청에는 `incident_id`, `message` 외에 **`service: null`**이 들어갔다. 실제 MCP 함수는 `send_customer_notice(incident_id, message)`만 받으므로 실행 시 다음 오류가 발생했다.

```text
service
  Unexpected keyword argument [type=unexpected_keyword_argument, input_value=None, input_type=NoneType]
```

- 재시작·재진단·공지 승인 이력은 정상이다.
- `summary.notice_result.status`는 `FAILED`이고 해당 인시던트 #3의 `NOTICE_SENT` 감사 기록은 없다. 단순 표시 누락이 아니라 실제 공지 호출 실패다.
- 작업 `status=DONE`은 처리 종료를 나타내며 공지 성공을 의미하지 않는다.
- 공지를 선택한 경우 보고서 뒤 공지 경로를 생략하는 `route_report`는 제안한 도구 이름으로 분기한다. 이번 실패 뒤 별도 공지 경로로 복구되지 않았다.
- 제품 코드는 이 실행에서 수정하지 않았다. 이 결함의 수정·재검증은 미완료다.

관련 코드:

- [공지 도구 시그니처](../../../server/python/business-mcp/src/business_mcp/server.py#L179)
- [제안 인자 처리](../../../server/python/agent/src/agent/graph.py#L585)
- [보고서 뒤 공지 분기](../../../server/python/agent/src/agent/graph.py#L773)
- [E2E 공지 성공 판정](../../../scripts/e2e.py#L832)

증거: [실패 작업 응답](b-hang-latency_task.json), [실행 이벤트](b-hang-latency_events.json), [실제 조치·공지 감사 기록](action_notice_audit.json).

## 실행 환경과 범위

- macOS Docker Compose 프로젝트 `opslens`, 로컬 kind `opslens-tenants`, ArgoCD `tenant-a`.
- 캐시 이미지의 의존성에 현재 Python 소스·프롬프트를 마운트하고, 현재 폴더에서 빌드한 Backend JAR·Frontend dist를 사용했다. 전체 이미지를 새로 빌드한 실행은 아니다.
- 실제 Prometheus/Loki 관측, Kafka, PostgreSQL, MCP, 모델 API, 장애 주입, 승인, Kubernetes 재시작을 사용했다.
- 모델 설정은 `gpt-5-mini` → 대체 `gpt-5-nano`, Jev는 비활성화했다. 실행 중 403 및 후속 성공 로그가 있다. 같은 키의 별도 API 확인에서 mini는 접근 권한 부족 403, nano는 200이었다. 호출별 응답 모델 메타데이터는 보존하지 않아 당시 각 호출의 실제 모델은 설정·로그에 근거한 추정이다. [환경 확인](model_environment.json)
- 고객 공지는 포털 저장 기능이다. 이관 `QUEUED`는 이관 기록이며 외부 Slack 발송 완료를 의미하지 않는다. 이번 실행에서 Slack 발송은 하지 않았다.
- 종료 후 장애·의존 장애 ConfigMap을 해제하고 부하 발생기 0, A사 HPA 기본 상태를 확인했다. ArgoCD는 `main / Synced / Healthy`, 임시 브랜치 없음, 테스트 인시던트 3개 모두 `RESOLVED`, 미완료 작업 0이다.
- 로컬 작업 URL은 실행 환경에서만 열리며 GitHub에서 호스팅되는 화면이 아니다. 생성 보고서는 실행 출력이며 서술·단위 정확도 전체를 보증하지 않는다.

## 재현 방법과 코드

기존 [E2E 코드](../../../scripts/e2e.py)와 [장애 주입 코드](../../../scripts/inject.py)를 그대로 사용했다. 테스트 핵심 로직과 제품 코드는 위 커밋과 동일하다. [소스 해시](source_manifest.json)

기본 포트(화면 80 / Backend 8080)와 저장소 `.env`를 사용하는 환경:

```bash
python3 scripts/e2e.py b-hang-latency a-dbslow-backlog b-pool-dbslow
```

포트·Compose 파일이 다른 환경에서는 [환경 지정 실행 스크립트](../../../scripts/run_compound_e2e.py)를 사용한다. 판정·승인·장애 주입 로직은 기존 모듈을 그대로 호출한다.

```bash
python3 scripts/run_compound_e2e.py \
  --api-url http://127.0.0.1:18080 --web-url http://127.0.0.1:18000 \
  --compose-project opslens --env-file /path/to/runtime.env \
  --compose-file docker-compose.yml --compose-file /path/to/runtime.override.yml
```

먼저 Compose와 kind를 준비해야 한다. 기본 실행은 위 복합 장애 3개다. A사 GitOps는 임시 브랜치를 만들고 기대한 PR을 승인·병합할 수 있으며 종료 시 복원한다. 인증 파일·API 키·개인 경로를 포함한 실행 환경 파일은 저장소에 포함하지 않는다.

## 결과 파일

- [누적 18개 판정 JSON](cumulative_results.json)
- [복합 장애 결과·정리 상태 JSON](compound_results.json)
- [복합 장애 실행 요약](summary.md), [전체 실행 로그](compound_run.log)
- [문서·질문·이상 없음 실행 로그](common_scenarios.log)
- 각 시나리오의 `.md`, `.log`, `_task.json`, `_events.json`에는 판정·출력·도구 실행 증거를 보관했다.

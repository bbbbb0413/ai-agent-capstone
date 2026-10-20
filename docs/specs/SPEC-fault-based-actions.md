# Spec: 장애 형태·시스템 구조 기반 조치

- 상태: **승인됨** (2026-10-19, 구현 전). 결정 사항은 9장
- 작성: 2026-10-19 (개정 3: 결정 사항 반영)
- 관련: [ADR 0001](../adr/0001-tenant-infra-actions-hybrid-environment.md), [기획안](../OpsLens-기획안.md) 3.3·3.6·6·10장, [Harness 명세](../harness-spec.md)

## 1. 배경: 지금 무엇이 문제인가

### 1.1 조치 근거가 고객사·시나리오에 묶여 있다

조치를 고르는 코드에는 고객사 이름으로 분기하는 부분이 없다. 문제는 AI가 근거로 쓰는 **런북이 고객사별·시나리오별로만 쓰여 있다**는 점이다. rag-mcp는 작업한 고객사의 문서만 검색한다(`WHERE tenant_id = …`).

| 장애 형태 | A사 런북 | B사 런북 |
|---|---|---|
| DB 커넥션 풀 고갈 (직전 설정 변경) | RB-A-01 롤백 | 없음 |
| 작업 스레드 고갈 (파드 멈춤) | 없음 | RB-B-02 재시작 |
| 외부 API 지연 | RB-A-02 공지 | RB-B-01 공지 |
| 트래픽 급증 (리소스 부족) | RB-A-02 확장 | 없음 |

B사에서 나는 장애가 A사에서 나면 근거 조항이 없다. 지금 E2E가 통과하는 이유는 시나리오와 런북이 1:1로 맞물려 있어서다.

### 1.2 시스템 구조를 보지 않는다

같은 증상이라도 고객사·서비스의 구조에 따라 맞는 조치와 조치의 영향이 다르다.

| 장애 | 증상만 보면 | 구조까지 보면 |
|---|---|---|
| 트래픽 급증 | 레플리카 확장 | 앱 파드가 병목일 때만 맞다. `레플리카 × 풀 크기`가 DB 최대 연결 수를 넘으면 확장이 DB 장애를 만든다 |
| 커넥션 풀 고갈 | 롤백 또는 재시작 | DB 자체가 느리거나 죽었으면 앱 조치는 소용없고, DB 담당에게 에스컬레이션해야 한다 |
| 파드 멈춤 | 재시작 | 레플리카가 1개면 재시작이 곧 전체 중단이다. 2개 이상이면 하나씩 재시작해 무중단으로 할 수 있다 |
| 외부 의존 지연 | 공지 | 서드파티면 공지, 고객사의 다른 팀 서비스면 그 팀에 에스컬레이션한다 |

지금 Agent가 아는 구조는 서비스 이름, 의존 대상 이름, 현재 레플리카 수와 이미지뿐이다. 아래 정보는 모른다.

- 의존 대상의 종류와 소유자
- 용량 제약(DB 최대 연결 수, 풀 크기)
- 의존 대상의 상태
- 무중단 재시작 가능 여부
- **실행 플랫폼**: `tenants.namespace` 칸 자체가 Kubernetes를 전제하고, backend는 항상 `KubernetesClient`를 부른다. Docker나 VM 고객사는 접속 오류로 실패하고, Agent는 이유를 모른다.

### 1.3 기존 구현의 결함

- `restart_pods`가 서비스의 파드를 **한꺼번에** 삭제한다. 레플리카가 2개인 A사도 재시작하는 순간 잠깐 전체가 중단된다.
- 확장(`scale_deployment`)으로 회복되는 장애가 데모 앱에 없다. 그래서 확장 조치와 확장 PR이 한 번도 실제로 실행되지 않았다.

## 2. 목표와 범위

**목표**

1. 조치를 **장애 형태**와 **시스템 구조**로 정한다. 고객사 이름이 아니다.
2. 구조 정보는 문서가 아니라 **서비스 카탈로그**(구조화된 데이터)에 둔다. 계산과 제약 검사는 backend가 한다.
3. 할 수 있는지는 계약과 구조 제약이, 어떻게 반영할지는 실행 플랫폼과 운영 방식이 정한다.
4. 계약이나 구조상 할 수 없는 조치가 필요하면, 조치 대신 **에스컬레이션**(필요하면 공지)을 제안한다.
5. 장애 형태 × 고객사 교차 매트릭스와 구조가 결과를 바꾸는 경우를 E2E로 검증한다.

**범위 밖**

- Kubernetes 외 실행 플랫폼(Docker, VM, ECS) 어댑터 구현. 카탈로그 항목과 확장 지점만 만든다(5.5).
- 실제 클라우드 리소스(노드 증설, RDS 등) 조치. 조치는 기존 3종(재시작, 확장, 롤백)만 다룬다.
- 런북에 없는 새 유형의 장애. 공통 런북에 없으면 "원인 미확정 → 에스컬레이션"으로 처리한다.
- GitHub App 전환과 단기 자격증명. 별도 작업이다.

## 3. 설계: 판단 4층

```
① 장애 형태   → 후보 조치          AI · 공통 런북 (증상으로 분류)
② 시스템 구조  → 적합성과 영향      AI 판단 + backend 계산 · 서비스 카탈로그, 서비스 런북
③ 계약        → 허용 여부          backend · 계약서, allowed_actions
④ 실행 환경    → 반영 방법          backend · 실행 플랫폼(runtime) + 운영 방식(GITOPS/DIRECT)
```

| 층 | 결정 주체 | 근거 | 지금 대비 |
|---|---|---|---|
| ① 장애 형태 → 후보 조치 | AI (`diagnose`, `propose_action`) | 공통 런북 RB-STD-* + 진단 근거 | **바뀜**: 고객사 런북 → 공통 런북 |
| ② 구조 검토 | AI(병목 위치, 영향 판단) + backend(안전 상한 계산·검사) | 서비스 카탈로그 + 서비스 런북 | **새로 추가** |
| ③ 계약 | backend (AI도 제안 전에 확인) | `allowed_actions`, 계약서 | 그대로. 불가 시 에스컬레이션 경로 추가 |
| ④ 반영 | backend (`ChangeService`) | 카탈로그의 `runtime`, `tenants.change_mode` | runtime으로 어댑터를 고르게 바뀜 |

**역할 정리:** AI는 무엇을 할지(의도)를 정한다. backend는 할 수 있는지(계약·구조 제약)와 어떻게 반영할지(플랫폼·운영 방식)를 정한다. 사람은 실행해도 되는지(승인)를 정한다.

## 4. 정보를 두는 곳

정보를 세 종류로 나누고, 종류마다 두는 곳을 정한다.

| 종류 | 예시 (order-api) | 두는 곳 | 이유 |
|---|---|---|---|
| 구조 사실 | 실행 플랫폼 Kubernetes, postgres 의존(A사 DBA 소유, 최대 연결 100), 풀 20, 레플리카 2~5, 무상태 | **서비스 카탈로그** | 숫자는 정확해야 하고 backend가 계산·검사에 쓴다. RAG로 문장에서 찾으면 틀릴 수 있다. 실제 Kubernetes 값과 대조할 수 있다 |
| 서비스별 절차·주의 | "DB 이상이면 A사 DBA에게 먼저 연락", "결제 처리 중 재시작 주의" | **서비스 런북** (RAG) | 서비스마다 다르고 표로 만들기 어려운 판단 지식이다 |
| 장애 형태별 일반 절차 | 커넥션 풀 고갈 → 직전 변경 확인 → 롤백 | **공통 런북** (RAG) | 모든 서비스·고객사에 같다. 서비스마다 쓰면 중복되고 서로 어긋난다 |
| 계약 | 허용 조치, 승인 절차, 공지 기한, 확장 상한 | **계약서** (RAG) + `allowed_actions` | 지금과 같다 |

런북은 구조 사실을 옮겨 적지 않는다. "카탈로그 참조"로 연결한다. 실제 운영의 서비스 카탈로그/CMDB(Backstage, ServiceNow)와 런북의 관계와 같다.

### 4.1 서비스 카탈로그

**위치:** backend DB (결정 5). 지금의 `services` 테이블을 확장하고 의존 대상 테이블을 추가한다. 마이그레이션으로 데모 값을 넣는다. 고객사 GitOps 저장소의 YAML(Backstage 방식)로 관리하는 방식은 운영 방향으로만 ADR에 적는다.

| 항목 | 예시 | 쓰임 |
|---|---|---|
| `runtime` | `kubernetes` (`docker`, `vm`, `ecs` …) | 가능한 조치 집합과 어댑터 선택 (④층) |
| `target` | 클러스터 `kind-opslens-tenants`, 네임스페이스 `tenant-a` | 어댑터가 접속할 곳. 지금의 `tenants.namespace`를 서비스 단위로 옮긴다 |
| `stateful` | `false` | 상태 저장 서비스면 재시작·확장을 에스컬레이션으로 돌린다 |
| `min_replicas`, `max_replicas` (계약) | 2, 6 | 확장 하한·상한 (③층) |
| `pool_size` | 20 | 안전 상한 계산 |
| 의존 대상 | `postgres`: 종류 `database`, 소유 `tenant`(A사 DBA), `max_connections` 100 / `payment-gateway`: 종류 `external_api`, 소유 `third_party` | 병목 판단, 에스컬레이션 대상 선택, 안전 상한 계산 |

**backend가 계산해서 함께 돌려주는 값** (`get_service_inventory`):

| 계산값 | 식 | 예시 |
|---|---|---|
| `safe_max_replicas` | min(계약 `max_replicas`, ⌊DB `max_connections` × 0.8 ÷ `pool_size`⌋) | min(6, ⌊100 × 0.8 ÷ 20⌋) = **4** |
| `zero_downtime_restart` | 현재 레플리카 ≥ 2 이고 무상태 | A사 true, B사 false |
| `supported_actions` | 플랫폼이 지원하는 조치 ∩ 계약 허용 조치 | A사 3종, B사 재시작만 |

0.8은 DB 연결의 20%를 관리·마이그레이션용으로 남기는 여유다(결정 6). 값은 카탈로그 항목으로 둔다. A사 상한은 계약 6, 구조 4다(결정 2).

### 4.2 문서 구성

| 파일 | 내용 |
|---|---|
| `data/documents/_common/runbook-standard.md` (새 문서) | 장애 형태별 증상·구분 기준·조치·구조 검토 절차 (4.3) |
| `tenant-a/sla-contract.md` | 지금 내용에 확장 상한(6)을 추가한다 |
| `tenant-a/runbook-order-api.md` | **서비스별 절차·주의만** 남긴다: GitOps 운영(PR 병합은 A사 담당), DB 이상 시 A사 DBA 연락, 공지 기한 15분 |
| `tenant-b/sla-contract.md` | 확장·롤백이 필요하면 B사 승인 요청(에스컬레이션)이라고 명시한다 |
| `tenant-b/runbook-reservation-api.md` | 서비스별 절차·주의만 남긴다: 단일 레플리카라 재시작 시 전체 중단(사전 공지), 결제 대행사 이름 미기재 |

고객사 문서의 조항 번호(RB-A-*, RB-B-*)는 서비스별 절차에만 쓴다. 조치 근거는 RB-STD-*로 통일한다.

### 4.3 공통 런북 조항

| 조항 | 장애 형태 | 대표 증상 | 구조 검토 (②층) | 조치 |
|---|---|---|---|---|
| RB-STD-01 | 설정 변경으로 인한 커넥션 풀 고갈 | 5xx 급증, `HikariPool … Connection is not available`, 이상 직전 `DEMO_POOL_SIZE` 감소 | DB 상태가 정상인지 확인한다. DB가 이상하면 RB-STD-05 | `rollback_deployment`. 직전 변경이 없으면 RB-STD-02 |
| RB-STD-02 | 작업 스레드 고갈 (파드 멈춤) | 5xx 급증, `503 worker pool saturated`, 직전 배포 없음 | `zero_downtime_restart`가 false면 영향(전체 중단)을 이유에 적고 공지를 함께 제안한다 | `restart_pods` |
| RB-STD-03 | 트래픽 급증 (앱 처리 용량 부족) | 에러는 정상인데 p95 상승, `request queue backlog`, 외부·DB 지연 로그 없음. 5xx가 함께 늘면 RB-STD-02다 | HPA가 관리하면 레플리카를 바꾸지 않는다(5.4.2). HPA가 확장 중이면 조치하지 않는다. 최대치에 닿았으면 `safe_max_replicas`까지 최대치를 올리고, 이미 상한이면 RB-STD-06. HPA가 없으면 목표 레플리카(현재의 2배)를 `safe_max_replicas`까지만 | HPA: `adjust_autoscaler` 또는 에스컬레이션. HPA 없음: `scale_deployment` |
| RB-STD-04 | 외부 의존 지연 | p95 상승, `payment API timeout` 등 외부 호출 경고 | 의존 대상 소유가 `third_party`면 공지, `tenant`면 해당 팀에 에스컬레이션 | 조치하지 않음 |
| RB-STD-05 | 의존 대상(DB) 자체 장애 | p95 상승 또는 5xx, `slow query … db=postgres`, 앱 풀 설정은 그대로 | 앱 조치(재시작·확장·롤백)는 효과가 없고, 확장은 DB 부하를 늘린다 | 조치하지 않음. 의존 대상 소유자에게 에스컬레이션하고, 고객 영향이 있으면 공지 |
| RB-STD-06 | 계약·구조상 할 수 없는 조치가 필요 | 위 조치가 `supported_actions`에 없거나 상한 초과 | — | 조치하지 않음. **에스컬레이션하고, 고객 영향이 있으면 공지도 한다** (결정 1) |
| RB-STD-07 | 원인 미확정 | 근거 부족 | — | 에스컬레이션 |

RB-STD-03(트래픽 급증), RB-STD-04(외부 지연), RB-STD-05(DB 장애)는 모두 지연으로 보인다. 그래서 어느 계층의 로그에 지연이 찍히는지와 의존 대상 상태로 구분한다는 기준을 조항에 적는다.

## 5. 코드 변경

### 5.1 서비스 카탈로그 (backend)

| 위치 | 변경 |
|---|---|
| 마이그레이션 `V8__service_catalog.sql` | `services`에 `runtime`, `target_cluster`, `target_namespace`, `stateful`, `min_replicas`, `max_replicas`, `pool_size`를 추가한다. 의존 대상 테이블 `service_dependencies(tenant_id, service, name, kind, owner, max_connections)`을 만든다. 데모 값을 넣는다 |
| `ChangeService.inventory` | 카탈로그 + 실시간 값(Kubernetes 레플리카·이미지) + 계산값(4.1)을 돌려준다 |
| `ChangeService.execute` | 실행 전에 `supported_actions`와 `safe_max_replicas`를 검사한다. 넘으면 422로 거부하고, 이유와 상한을 메시지에 담는다. 계약 검사와 같은 방식이다 |
| 의존 대상 상태 | `GET /internal/tenants/{t}/dependencies/{name}/health`. 데모에서는 앱이 내보내는 의존 대상 지연 메트릭(`demo_dependency_seconds`)을 Prometheus에서 조회해 정상·지연·장애로 돌려준다 |

### 5.2 실행 어댑터 (backend, ④층)

| 위치 | 변경 |
|---|---|
| `RuntimeAdapter` 인터페이스 (새) | `restart`, `scale`, `rollback`, `history`, `status`. `KubernetesClient`가 이 인터페이스를 구현한다 |
| 어댑터 선택 | `ChangeService`가 카탈로그의 `runtime`으로 고른다. 없는 runtime이면 "지원하지 않는 실행 환경(docker)" 422를 돌려주고 Agent가 에스컬레이션한다 |
| 순차 재시작 | Kubernetes 어댑터의 `restart`를 바꾼다. 레플리카가 2개 이상이면 파드를 하나씩 지우고, 새 파드가 Ready가 된 뒤 다음 파드를 지운다(파드당 최대 60초). 1개면 지금처럼 바로 지운다 |
| GitOps 경로 | 지금처럼 `change_mode`로 PR/API를 나눈다. PR 대상 파일은 runtime이 kubernetes일 때만 존재한다 |

### 5.3 RAG (공통 문서)

| 위치 | 변경 |
|---|---|
| `indexer` | `data/documents/_common/`을 `tenant_id = '_common'`으로 색인한다 |
| `rag-mcp search_documents` | `WHERE tenant_id IN (요청 고객사, '_common')`. 고객사는 계속 헤더로만 정한다 |
| `rag-mcp read_document` | `docs://_common/…`은 모든 고객사가 읽을 수 있다. 다른 고객사 문서는 계속 거부한다 |

### 5.4 Agent

| 위치 | 변경 |
|---|---|
| `harness.NODE_TOOLS` | `propose_action`에 `business_escalate_incident`를 추가한다. `diagnose`에 `business_get_dependency_health`를 추가한다 |
| `business-mcp` | `get_dependency_health(name)` 도구를 추가한다. `get_service_inventory` 설명에 카탈로그 항목과 계산값을 적는다 |
| `prompts/diagnose.md` | 병목 위치(앱, DB, 외부 API)를 판단하는 단계를 추가한다. 진단 결과에 `장애 형태: RB-STD-0x`와 `병목: 앱|DB|외부` 두 줄을 추가한다 |
| `prompts/propose.md` | ① 공통 런북으로 후보 조치를 정한다 → ② 카탈로그로 검토한다(병목 위치, `safe_max_replicas`, `zero_downtime_restart`) → ③ `supported_actions`에 있는지 확인한다 → ④ 불가하면 RB-STD-06으로 에스컬레이션하고, 고객 영향이 있으면 보고서 뒤 공지 승인으로 이어진다 |
| `retrieve_runbook` | 검색어에 장애 형태를 넣는다. 공통 런북, 서비스 런북, 계약 조항이 함께 나오게 한다 |
| 승인 카드 (`_approval_request`) | 영향 정보(`impact`)를 넣는다: 실행 환경, 현재·준비 레플리카, 재시작 방식(`restartMode`: `ROLLING`·`ALL_AT_ONCE`), 확장 목표와 안전 상한. 값은 서비스 목록의 backend 계산값을 옮긴다 (`graph._action_impact`, E2E `a-hang`) |

backend 검사(계약·구조 제약)는 AI가 잘못 골라도 막는 두 번째 방어선이다. AI의 제안 기준과 backend의 검사 기준은 같은 카탈로그 값을 쓴다.

### 5.4.1 에스컬레이션 (발송 직전까지, 결정 7)

`escalate_incident(incident_id, category, reason, service?, required_action?, blocked_by?, dependency?)`를 받은 backend(`EscalationService`)가 요청을 검사하고, 받는 사람과 메시지를 정해 발송 대기열 `escalations`(status `QUEUED`)에 넣는다. 실제 발송(메신저·전화·메일)은 하지 않는다. 발송기를 붙이면 `QUEUED`를 읽어 보내고 `SENT`·`FAILED`로 바꾼다.

| 분류 (`category`) | 누가 정하나 | 받는 사람 (MSP 담당은 항상) | 필수 인자 |
|---|---|---|---|
| `P1` | 흐름 (triage) | — | — |
| `NOT_RECOVERED` | 흐름 (재진단 후 미회복) | — | — |
| `CHANGE_TIMEOUT` | 흐름 (PR 병합 기한 초과) | — | — |
| `ACTION_BLOCKED` | AI (`propose_action`, RB-STD-06) | + 고객사 담당 (`tenants.customer_contact`) | `required_action`, `blocked_by` (`CONTRACT`·`STRUCTURE`·`RUNTIME`) |
| `DEPENDENCY` | AI (RB-STD-04·05) | + 의존 대상 소유자 (카탈로그 `owner_contact`) | `dependency`. 서드파티 소유면 422 (공지 대상이다) |
| `CAUSE_UNKNOWN` | AI (RB-STD-07) | — | — |

- 같은 인시던트에 같은 분류·조치·의존 대상이 이미 대기 중이면 새로 쌓지 않고 그 건을 돌려준다(`duplicate`).
- 감사 로그 `ESCALATED`에 대기열 번호와 받는 사람을 남긴다. MSP 직원은 `GET /api/incidents/{id}/escalations`로 대기열을 본다. 고객사 포털에는 보이지 않는다.
- 승인 카드에는 분류·필요한 조치·할 수 없는 이유를, 작업 요약에는 받는 사람과 "호출 대기열에 등록 (발송 미연결)"을 보여 준다. 보고서는 "호출했다"고 쓰지 않는다.

### 5.4.2 자동 확장(HPA)과 Agent의 역할 (결정 8)

트래픽에 맞춰 파드 수를 바꾸는 일은 HPA가 한다. Agent가 HPA 대상의 레플리카를 직접 바꾸면 HPA가 곧 덮어쓰고, GitOps 매니페스트의 `replicas`와도 충돌한다. Agent는 HPA가 스스로 못 하는 판단을 맡는다: **HPA 최대치가 모자랄 때, 구조 제약(안전 상한) 안에서 최대치를 올린다.**

| 상황 | HPA | Agent |
|---|---|---|
| 급증, HPA가 최대치 아래에서 늘리는 중 | 늘린다 | 조치하지 않는다. 보고서에 자동 확장 중이라고 적는다 |
| 급증, HPA가 최대치이고 최대치 < `safeMaxReplicas` | 더 늘리지 못한다 | `adjust_autoscaler`로 최대치를 `safeMaxReplicas`까지 올린다 (GITOPS: PR) |
| 급증, HPA가 최대치이고 최대치 = `safeMaxReplicas` | 더 늘리지 못한다 | 에스컬레이션 (`ACTION_BLOCKED`, `STRUCTURE`). 더 올리면 DB 연결이 모자라 A사 DB 용량 판단이 필요하다 |
| DB 자체 지연 | CPU가 오르지 않아 늘리지 않는다 | 에스컬레이션 (`DEPENDENCY`, A사 DBA) |
| HPA 없음, 계약상 확장 불가 (B사) | 해당 없음 | 에스컬레이션 (`ACTION_BLOCKED`, `CONTRACT`) |

- "최대치에 닿았다"는 HPA의 목표 레플리카(`desiredReplicas`)로 본다. 새 파드가 아직 뜨는 중이어도 목표가 최대치면 닿은 것이다.
- HPA 최소치는 트래픽 대응으로 바꾸지 않는다. 최소치는 예정된 행사 전 사전 확장이나 기본 용량 계획에 쓰는 값이라 장애 대응 범위 밖이다.
- 최대치를 내리는 조치는 두지 않는다. 부하가 빠지면 HPA가 스스로 줄인다. 올린 최대치를 원래대로 돌릴지는 A사가 정한다(보고서 권장 행동에 적는다).

| 위치 | 변경 |
|---|---|
| `gitops/tenants/tenant-a` | `hpa.yaml`: order-api 최소 2, **최대 3**, CPU 사용률 목표 100%(평상시 약 50%), 축소 안정화 60초. A사가 비용 때문에 평소 최대 3으로 운영한다는 설정이다(안전 상한 4, 계약 상한 6). Deployment에서 `replicas`를 빼서 ArgoCD가 HPA와 다투지 않게 한다 |
| `scripts/cluster-up.sh` | metrics-server 설치 (kind라 `--kubelet-insecure-tls`) |
| `gitops/cluster/tenants.yaml` | 고객사 ServiceAccount Role에 `autoscaling/horizontalpodautoscalers` get·list·patch |
| 데모 앱 | 처리 슬롯에서 실제로 CPU를 쓴다(주문당 약 5ms). sleep만 하면 CPU가 오르지 않아 HPA가 반응하지 않는다. 파드 CPU 요청 100m |
| `RuntimeAdapter.Workload` | `autoscaler`(최소, 최대, 현재·목표 레플리카, CPU 목표·현재). Kubernetes 어댑터가 HPA를 읽는다. `RuntimeAdapter.setAutoscalerMax` 추가 |
| `ChangeService` | 새 조치 `adjust_autoscaler(service, max_replicas)`: HPA가 있어야 하고, 최대치는 현재 최대치보다 크고 `safeMaxReplicas` 이하여야 한다. GITOPS면 `hpa.yaml`의 `maxReplicas`를 고치는 PR, 아니면 어댑터가 HPA를 바로 고친다. HPA가 관리하는 서비스의 `scale_deployment`는 422("HPA가 관리한다. 최대치가 모자라면 adjust_autoscaler"). 인벤토리에 `autoscaling`(`atMax` 포함)을 넣고 `supportedActions`를 HPA 유무에 맞춘다 |
| 계약 | A사 `allowed_actions`에 `adjust_autoscaler`를 추가한다 (마이그레이션). A사 계약서에 "HPA 최대치는 계약 상한 안에서 MSP가 PR로 조정한다"를 넣는다 |
| `business-mcp` | `execute_runbook_action`의 action에 `adjust_autoscaler`, 인자 `max_replicas` |
| 공통 런북 RB-STD-03 | 위 표의 판단 순서. `prompts/propose.md`도 같다. HPA가 확장 중이면 조치하지 않는다(이때만 도구를 부르지 않는다) |
| 부하 발생기 | A사 +250건/초: 파드 3개(약 210건/초)는 넘고 4개(약 280건/초)면 감당한다 |

`scale_deployment`와 `GitOpsRepository.scale`(확장 PR)은 HPA가 없는 서비스용으로 남긴다.

### 5.5 실행 플랫폼 확장 지점 (구현하지 않음)

| 플랫폼 | 재시작 | 확장 | 롤백 | 접속 |
|---|---|---|---|---|
| Kubernetes (구현) | 파드 순차 삭제 | `/scale` 또는 PR | ReplicaSet 또는 PR | 고객사 ServiceAccount |
| Docker 단독 | 컨테이너 재시작 | 지원 안 함 | 이전 이미지 태그로 재생성 | 원격 Docker API(TLS) |
| VM | 서비스 재시작 (systemd) | 지원 안 함 | 이전 패키지·아티팩트 | SSH·Ansible, 접속 대행 |
| ECS | 태스크 교체 | 서비스 desired count | 이전 태스크 정의 | AssumeRole |

카탈로그의 `runtime`과 이 표의 "지원 안 함"이 `supported_actions` 계산에 들어간다. 이 표는 ADR에 결정 근거로 남긴다.

### 5.6 데모 앱

**처리 용량 모델 (트래픽 급증용)**

- 파드마다 처리 슬롯을 둔다(`DEMO_WORKERS`, 기본 4). 주문 하나가 슬롯을 50ms 안팎 점유해서, 파드당 용량은 초당 약 80건이다.
- `POST /orders` 엔드포인트를 추가한다. 자체 생성 트래픽도 같은 슬롯을 쓴다.
- 슬롯 대기가 200ms를 넘으면 `WARN worker queue backlog: waiting=…, active=4/4`를 남긴다.
- 메트릭은 대기를 포함해 잰다. 과부하면 p95가 1초를 넘어 기존 `HighLatency` 알림이 뜬다. 에러는 내지 않는다.

**의존 대상 장애 (DB 자체 지연용)**

- 장애 `DB_SLOW`: 주문마다 DB 호출 지연이 800~1500ms가 된다.
- 로그는 `WARN slow query: SELECT … took 1200ms (db=postgres)`이고, 풀 설정은 그대로다.
- 메트릭 `demo_dependency_seconds{dependency="postgres"}`를 추가한다. `PAYMENT_LATENCY`는 같은 메트릭에 `dependency="payment-gateway"`로 찍는다.

**부하 발생기**

- 같은 데모 이미지를 부하 발생 모드(`DEMO_MODE=loadgen`)로 띄운다. 네임스페이스는 `traffic`(외부 사용자 역할)이다.
- 고객사별 Deployment `loadgen-tenant-a`, `loadgen-tenant-b`를 레플리카 0으로 둔다. 주입은 레플리카를 1로 올리는 것이다.
  - ArgoCD 관리 경로 밖이라 selfHeal에 되돌려지지 않는다.
  - 고객사 ServiceAccount로는 건드릴 수 없다.
- 메트릭 수집 대상이 아니고, 로그도 Loki로 보내지 않는다.

| | 평상시 | 급증 시 | 확장 후 |
|---|---|---|---|
| A사 (레플리카 2, 용량 약 160건/초) | 약 10건/초 | +200건/초, 파드당 약 105건/초로 과부하 | `safe_max_replicas` 4까지, 파드당 약 52건/초로 정상 |
| B사 (레플리카 1, 용량 약 80건/초) | 약 5건/초 | +120건/초로 과부하 | 계약상 확장 불가 → 에스컬레이션 |

실측 (2026-10-20, 부하 발생기 동시 요청 상한 300, 30초 창):

| 조건 | p95 | 처리량 | 비고 |
|---|---|---|---|
| 평상시 | 0.13초 | A 9건/초, B 4건/초 | |
| B사 +120건/초 | 3.9초 | 80건/초 | 에러 0, 건너뛴 요청 약 40건/초 |
| A사 +200건/초, 레플리카 2 | 2.4초 | 150건/초 | 파드당 66~70건/초 |
| A사 +200건/초, 레플리카 4 | 0.15초 | 200건/초 | 파드당 약 55건/초, 에러 0 |

구현하며 바꾼 것:
- 주문 요청은 Tomcat 스레드가 아니라 주문 전용 스레드에서 처리한다. 과부하에서 Tomcat 스레드가 모두 슬롯 대기에 묶여 `/actuator/health`·`/actuator/prometheus`가 응답하지 못했다. 파드가 Service에서 빠지고 메트릭이 끊겼다.
- 부하 발생기는 응답을 기다리는 요청을 300건으로 제한한다. 상한이 없으면 서버 대기 요청이 끝없이 늘어 JVM 힙이 터졌다(`OutOfMemoryError`).
- 부하 발생기는 1초마다 새 HTTP 클라이언트를 쓴다. Service는 연결 단위로 파드를 고른다. 처음 맺은 연결만 재사용하면 레플리카를 4로 늘려도 새 파드에 초당 3건 안팎만 갔다(파드별 처리량으로 확인).
- 의존 대상(DB, 결제 API) 호출은 처리 슬롯 밖에서 한다. 의존 대상이 느려도 대기열 경고가 나오지 않아야 장애 형태가 구분된다.
- 대기열 경고 문구는 `request queue backlog`이다. 처음의 `worker queue backlog`는 파드 멈춤의 `worker pool saturated`와 비슷해, B사 급증 측정 중 Agent가 RB-STD-02(재시작)를 제안했다.

### 5.7 장애 주입 스크립트

`scripts/inject.sh <tenant> <fault|clear>` 하나로 합친다. 기존 `scenario-a.sh`, `scenario-b.sh`는 이 스크립트를 부르는 래퍼로 남긴다.

| 장애 키 | A사 (GITOPS) | B사 (DIRECT) |
|---|---|---|
| `pool` 커넥션 풀 고갈 | 설정 저장소에 풀 5 커밋 | `kubectl set env DEMO_POOL_SIZE=5` (B사 운영자의 잘못된 배포) |
| `hang` 작업 스레드 고갈 | `/admin/faults` WORKER_HANG | 같음 |
| `surge` 트래픽 급증 | `loadgen-tenant-a` 레플리카 1 | `loadgen-tenant-b` 레플리카 1 |
| `latency` 외부 API 지연 | `/admin/faults` PAYMENT_LATENCY | 같음 |
| `dbslow` DB 자체 지연 | `/admin/faults` DB_SLOW | 같음 |

A사 `pool` 주입 커밋은 `main`이 아니라 E2E 임시 브랜치에 한다(5.8).

### 5.8 E2E 임시 브랜치 (결정 4)

E2E가 만드는 커밋(장애 주입, 롤백·확장 PR, 확장 원복 PR)은 `main` 기록에 남기지 않는다. ArgoCD는 Git에 있는 것만 반영하므로 커밋 자체는 피할 수 없다. 그래서 실행마다 임시 브랜치를 쓰고 지운다.

1. 시작할 때 `main`에서 `e2e/<시각>` 브랜치를 만든다.
2. A사의 설정 저장소 브랜치를 그 브랜치로 바꾼다.
   - ArgoCD Application `tenant-a`의 `targetRevision`
   - backend 고객사 설정의 `gitops_branch` (새 칸, 기본 `main`): PR의 base와 커밋 조회 기준이다. 관리자 API로 바꾼다
3. 장애 주입 커밋, 롤백·확장 PR과 병합이 모두 그 브랜치에서 일어난다.
4. 끝나면(실패·중단 포함) 두 설정을 `main`으로 되돌리고, ArgoCD가 `main`과 같아졌는지 확인한 뒤 브랜치를 지운다.

| 변경 위치 | 내용 |
|---|---|
| 마이그레이션 | `tenants.gitops_branch TEXT NOT NULL DEFAULT 'main'` |
| `GitOpsRepository` | 설정값 `base-branch` 대신 고객사의 `gitops_branch`를 쓴다 |
| 관리자 API | `PUT /api/admin/tenants/{id}/gitops-branch` (MSP_ADMIN, 감사 로그). 화면에는 노출하지 않는다 |
| `scripts/e2e.py` | 브랜치 생성·전환·복원·삭제. 복원은 `finally`에서 하고, 다음 실행의 사전 점검에서 남은 `e2e/*` 브랜치와 `main`이 아닌 설정을 확인한다 |

임시 브랜치의 PR도 GitHub에는 닫힌 PR로 남는다. 브랜치를 지워도 PR 기록은 지울 수 없다.

## 6. E2E 매트릭스

### 6.1 장애 × 고객사

| 시나리오 키 | 장애 | 고객사 | 기대 조치 | 반영 | 확인 포인트 |
|---|---|---|---|---|---|
| `a-pool` (지금 `a`) | 풀 고갈 | A | `rollback_deployment` | 되돌리기 PR → 병합 → 동기화 | 회복 |
| `a-hang` | 스레드 고갈 | A | `restart_pods` | GitOps여도 **API로 바로, 순차 재시작** | 재시작 중 에러율이 기준 아래 (무중단) |
| `a-surge` | 트래픽 급증 (+250건/초) | A | `adjust_autoscaler` **최대 4** (안전 상한) | PR → 병합 → 동기화 → HPA가 4로 확장 | 회복. `scale_deployment` 제안 없음, 4를 넘는 최대치 없음 |
| `a-surge-max` | 트래픽 급증 (+400건/초) | A | 위와 같이 최대 4로 올린 뒤에도 미회복 → **에스컬레이션** (`ACTION_BLOCKED`, `STRUCTURE`) + 공지 | 위와 같음 | 4를 넘는 제안 없음 |
| `a-latency` | 외부 API 지연 | A | 고객 공지 | — | 앱 조치 없음 |
| `a-dbslow` | DB 자체 지연 | A | **에스컬레이션** (A사 DBA) + 공지 | — | 앱 조치·확장 없음 |
| `b-pool` | 풀 고갈 | B | **에스컬레이션** (롤백 계약상 불가) + 공지 | — | 롤백 제안 없음 |
| `b-hang` (지금 `b`) | 스레드 고갈 | B | `restart_pods` + 전체 중단 영향 표시 | API로 바로 | 승인 카드에 영향 표시 |
| `b-surge` | 트래픽 급증 | B | **에스컬레이션** (확장 계약상 불가) + 공지 | — | 확장 제안 없음 |
| `b-latency` (지금 `b2`) | 외부 API 지연 | B | 고객 공지 | — | 재시작 없음 |

### 6.2 backend 방어선 (Agent를 거치지 않는 API 검사)

| 키 | 확인 |
|---|---|
| `guard-scale` | A사에 `scale_deployment`를 직접 요청 → 422, 메시지에 HPA가 관리한다는 내용과 범위(2~4) |
| `guard-runtime` | 카탈로그 runtime을 `docker`로 둔 테스트 서비스에 재시작 요청 → 422 "지원하지 않는 실행 환경" |
| `guard-contract` | B사에 `rollback_deployment` 요청 → 422 (지금도 동작, 회귀 확인) |

### 6.3 공통 판정 항목

- 기대한 조치를 제안했고, 제안이 한 번뿐이다
- 계약·구조상 불가한 조치를 제안하지 않았다
- 근거 조항이 RB-STD-*이고, 진단 결과의 병목 위치가 맞다
- 끝난 뒤 원래 상태다: 풀 20, 레플리카 원복, 부하 발생기 0, 주입 장애 해제

**확장 원복:** HPA가 부하가 빠지면 스스로 줄인다(기본 안정화 5분). 스크립트는 레플리카 2로 돌아오기를 기다리기만 한다. 결정 3의 원복 PR은 필요 없어졌다(결정 8).

**실행 시간:** 시나리오당 3~5분, 전체 12개는 40~50분으로 예상한다. 기존 키(`a`, `b`, `b2`)는 별칭으로 남긴다. PR이 없는 빠른 확인용 묶음 `--quick`은 `a-hang b-surge a-dbslow guard-*`이다.

## 7. 구현 순서

단계마다 확인한 뒤 다음으로 넘어간다.

| 단계 | 내용 | 확인 방법 |
|---|---|---|
| 1 | 공통 런북·서비스 런북·계약서 정리, RAG 공통 검색 | 재색인 후 A·B 작업 모두 RB-STD가 검색되고, B사 작업에서 A사 문서가 나오지 않는다 |
| 2 | 서비스 카탈로그 마이그레이션, 인벤토리 계산값, backend 제약 검사, `RuntimeAdapter`, 순차 재시작 | `guard-*` 3개 통과. A사 재시작 중 에러율이 기준 아래다 |
| 3 | 프롬프트 수정, 에스컬레이션 허용(5.4.1), 승인 카드 영향 표시 | 기존 E2E(`a b b2`)가 그대로 통과한다 (회귀 확인) |
| 4 | 데모 앱 처리 슬롯·`/orders`·대기열 경고·`DB_SLOW`·의존 메트릭, 부하 발생기, 의존 상태 도구(`get_dependency_health`, 의존 메트릭이 생겨야 의미가 있어 3단계에서 옮겼다) | 부하를 넣으면 p95가 1초를 넘고, 수동으로 레플리카 4로 늘리면 내려가는지 직접 잰다. 수치를 조정한다 |
| 5 | 장애 주입 스크립트 통합, E2E 임시 브랜치(`gitops_branch`, 관리자 API, ArgoCD 전환) | 장애 5종 × 2곳 주입·정리가 각각 동작한다. E2E 1회 실행 후 `main` 기록에 새 커밋이 없고 `e2e/*` 브랜치가 남지 않는다 |
| 6 | E2E 매트릭스 12개, 확장 원복 | 전부 통과한다. 실패하면 원인을 Harness 명세에 기록한다 |
| 7 | 기획안 3.3·10장, ADR 0001 D7·D8, Harness 명세 갱신 | 문서와 코드가 맞는지 확인한다 |

ADR에 추가할 결정:

- **D7.** 조치 근거는 MSP 공통 런북이고, 고객사 문서는 계약과 서비스별 절차만 둔다.
- **D8.** 시스템 구조는 서비스 카탈로그(구조화된 데이터)에 두고, 구조 제약은 backend가 계산·검사한다. 실행 플랫폼은 카탈로그 항목이며, 어댑터로 확장한다.
- 기각한 대안: 구조를 런북 문장으로 두기(값이 부정확하고 검사할 수 없다), 조치를 코드 규칙으로 고정하기(Agent의 판단이 없어진다).

## 8. 위험과 대응

| 위험 | 대응 |
|---|---|
| 지연 3종(트래픽 급증, 외부 지연, DB 장애)을 AI가 헷갈린다 | 로그 증상을 계층별로 다르게 만든다. 진단 시작 때 코드가 로그를 미리 조회한다(구현됨). 의존 대상 상태 도구로 DB를 직접 확인한다. 공통 런북에 구분 기준을 적는다 |
| 순차 재시작이 길어져 실행 시간 한도(120초)를 넘는다 | 재시작 대기는 Agent가 일하는 시간이 아니다. 도구 호출 안에서 기다리되, 레플리카 수 × 60초 상한을 두고, 한도 계산에서 빼는 방법(`Context.settle`과 같은 방식)을 구현 때 정한다 |
| 확장 후 새 파드가 늦게 떠서 회복 확인이 일찍 실패한다 | 동기화 알림은 Healthy일 때 온다. 회복 확인은 메트릭이 비면 한 번 더 기다린다(구현됨). 실측 후 조정한다 |
| 부하 발생기가 로컬 PC 자원을 많이 쓴다 | 동시 요청 수에 상한을 두고, 끝나면 0으로 되돌린다. E2E 정리 단계에서 확인한다 |
| 임시 브랜치 복원에 실패해 ArgoCD나 backend가 `e2e/*`를 계속 본다 | 복원을 `finally`에서 하고, 사전 점검에서 확인한다. 수동 복원 명령을 `docs/testing.md`에 적는다 |
| 계약상 불가할 때 에스컬레이션 대신 공지만 고른다 | 판정 항목에 넣고, 실패하면 프롬프트를 조정한다 |

## 9. 결정 사항 (2026-10-19)

| # | 질문 | 결정 | 반영 위치 |
|---|---|---|---|
| 1 | 계약·구조상 불가할 때 기대 동작 | **에스컬레이션과 공지를 둘 다** 한다 (공지는 고객 영향이 있을 때) | 4.3 RB-STD-05·06, 5.4, 6.1 |
| 2 | A사 확장 상한 | 계약 상한 6, 구조 상한 4 그대로 | 4.1, 6.1 `a-surge` |
| 3 | 확장 원복 방식 | ~~스크립트가 원복 PR을 만들어 병합한다~~ → 결정 8로 HPA가 줄인다 | 6.3 |
| 4 | E2E 테스트 커밋 | **`main`에 커밋하지 않는다.** 실행마다 임시 브랜치를 쓰고 지운다 | 5.7, 5.8, 7장 5단계 |
| 5 | 카탈로그 위치 | **backend DB**. GitOps YAML은 ADR에 운영 방향으로만 적는다 | 4.1, 5.1 |
| 6 | DB 연결 여유율 | **0.8** | 4.1 |
| 7 | 에스컬레이션 실제 발송 | **구현하지 않는다.** 받는 사람·메시지 결정과 발송 대기열 저장까지 한다 | 5.4.1 |
| 8 | 트래픽 급증 대응 (2026-10-20) | **A사는 HPA(최소 2, 최대 3)가 확장한다.** Agent는 레플리카를 바꾸지 않는다. HPA가 최대치에 닿으면 안전 상한(4)까지 최대치를 올리는 PR(`adjust_autoscaler`)을 제안하고, 이미 상한이면 에스컬레이션한다. 최소치는 바꾸지 않는다 | 5.4.2, 4.3 RB-STD-03, 6.1 `a-surge` |

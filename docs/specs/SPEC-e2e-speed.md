# Spec: E2E 실행 시간 줄이기

- 상태: **구현 완료, 검증 전** (2026-10-23). 결정 4개를 모두 추천안으로 정했다 (9장). Docker 메모리 10GB
- 근거: E2E 실행 기록 `.local/e2e/20261023-105347`, `20261023-131247`(15개 통과, 약 27분), [SPEC-compound-faults](SPEC-compound-faults.md)(18개, A사 묶음 약 40분 예상), [장애 처리 흐름](../장애-처리-흐름.md)
- 원칙: **판정 기준은 바꾸지 않는다.** 시나리오가 기대하는 Agent의 판단과 조치는 그대로이고, 기다리는 시간과 순서만 줄인다. 운영 동작을 바꾸는 항목(②, ⑤)은 운영에서도 맞는 이유가 있어야 한다. 단계마다 전체 E2E로 시간을 재고 따로 커밋한다.

## 1. 목표

| 항목 | 지금 | 목표 |
|---|---|---|
| 전체 18개 | 약 40~45분 (A사 묶음이 결정) | **약 10~12분** |
| 판정 | 18개 통과 | 18개 통과, 기대값 변경 없음 |
| 개발 중 확인 | 전체 또는 `--quick` | 고친 곳에 맞는 시나리오만 (⑥) |

## 2. 지금 시간이 드는 곳

전체 시간은 가장 긴 묶음(A사)이 정한다. 15개 실행(`20261023-131247`)의 A사 묶음은 7개, 약 25분이었고, 복합 시나리오 2개가 더해져 9개, 약 40분이 된다. B사 묶음은 5개, 약 17분이다.

시나리오 하나(약 190초)의 구성 (a-latency 실측):

| 구간 | 시간 | 이유 | 줄이는 항목 |
|---|---|---|---|
| 앞 인시던트가 닫히기를 기다림 | 15~70초 | 알림 해제가 Alertmanager `group_interval 1m` 주기로 온다 | ⑤ |
| 조용한 구간 (`QUIET_SECONDS = 75`) | 정리 후 75초까지 | Agent가 인시던트 시작 2분 전부터 로그를 봐서, 앞 장애 로그가 진단 근거에 섞인다 | ② |
| 주입 → 인시던트 생성 | 약 70초 | `rate[1m]`, `for: 30s`, 평가 주기 5초, `group_wait 10s` | ⑤ |
| Agent 진단·승인·조치·보고서 | 약 50초 | LLM 호출 | 범위 밖 |
| 해제 (A사 확장 시나리오) | 약 150초 | HPA가 레플리카 2로 줄 때까지 기다림 (축소 안정화 60초) | ④ |

같은 고객사 안에서는 순서대로만 돈다. 서비스가 고객사마다 하나라 장애를 겹쳐 넣으면 증상이 섞이기 때문이다. SPEC-compound-faults로 인시던트 묶기·관측·회복 확인이 모두 서비스 단위가 됐으므로, **서비스가 다르면** 같은 고객사 안에서도 동시에 돌 수 있다 (①).

## 3. ⑥ 고친 곳에 맞는 시나리오만 돌린다 (코드 변경 작음)

- `testing.md`에 "고친 곳 → 돌릴 시나리오" 표를 둔다.

| 고친 곳 | 돌릴 시나리오 |
|---|---|
| `prompts/diagnose.md`, `signals.py`, `action_guards.py` | `--quick` + 복합 3개 |
| `prompts/propose.md` | `--quick` + `a-surge-max` `b-pool` |
| backend 조치·계약 (`ChangeService`, `RuntimeAdapter`) | `guard` `a-pool` `b-pool` `b-surge` |
| backend 알림·인시던트 | `a-hang-latency` `b-hang` |
| 문서 저장소·색인 | `documents` `question` |
| 권한·토큰 | `isolation` `guard` |
| 데모 앱·`inject.py` | 고친 장애 종류의 A사·B사 시나리오 |

- `scripts/e2e.py --lane <공통|tenant-a|tenant-b>` 옵션을 더한다. 한 묶음만 돈다.
- 전체 18개는 커밋 전에 한 번만 돌린다(지금 규칙과 같다).

## 4. ② 조용한 구간을 없앤다 (agent, E2E)

**지금:** Agent는 로그를 "인시던트 시작 2분 전"부터 본다(`signals.LOG_LEAD`). 알림은 이상이 시작되고 1분 남짓 뒤에 울리므로 그만큼 앞을 봐야 한다. 그런데 같은 서비스에서 앞 장애가 그 2분 안에 끝났으면 앞 장애의 로그가 근거에 섞인다. 그래서 E2E가 정리 후 75초를 기다린다. 운영에서도 같은 일이 생긴다(앞 장애 직후의 새 장애).

**바꿀 것:**

- `signals.log_window_start(now, incident, previous_resolved_at)`: 지금 값과 "같은 서비스의 직전 인시던트가 닫힌 시각" 중 늦은 쪽에서 시작한다.
- `load_context`가 `business_list_incidents(status="RESOLVED")`에서 같은 서비스의 가장 최근 `resolvedAt`을 찾아 `context.previous_resolved_at`에 둔다. 새 도구는 필요 없다.
- 원인 후보별 사실(`cause_facts`)의 신호 집계도 같은 시작 시각을 쓴다.
- E2E: `QUIET_SECONDS`를 없앤다. 앞 인시던트가 닫히기를 기다리는 것(`wait_for_closed_incidents`)은 남긴다. 열린 인시던트가 있으면 새 알림이 거기에 묶이기 때문이다(SPEC-compound-faults 5장).

**예상:** 시나리오당 최대 60초, A사 묶음 약 8분.

**확인:** 단위 테스트(직전 인시던트가 닫힌 시각이 창을 자른다, 없으면 지금과 같다), 앞 장애 직후 다음 장애를 넣는 A사 묶음 E2E.

## 5. ④ 해제 대기를 필요한 곳에서만 한다 (E2E)

**지금:** A사 시나리오가 끝날 때마다 `wait_for_a_normal()`이 HPA 최대 3 복원 커밋의 동기화와 레플리카 2로의 축소를 기다린다(최대 300초).

**바꿀 것:**

- `reset()`은 설정만 되돌린다(관리 API 장애, 부하 발생기, 풀 크기, HPA 최대 3 커밋). 축소는 기다리지 않는다.
- 시나리오마다 `needs_normal`을 둔다. 평소 크기에서 시작해야 하는 시나리오만 시작 전에 기다린다.

| 시나리오 | 시작 전에 필요한 상태 |
|---|---|
| `a-surge`, `a-surge-max`, `a-dbslow-backlog` | HPA 최대 3 동기화, 레플리카 2 (HPA가 최대에 닿아야 최대치 조정을 제안한다) |
| `guard` | HPA 최대 3 동기화 (거부 전후의 레플리카를 비교하므로 축소가 끝나지 않아도 비교는 된다. 그 사이 HPA가 바꾸지 않게 축소 완료를 기다린다) |
| 그 밖 | 기다리지 않는다 |

- 확장 시나리오를 묶음 끝 쪽에 모은다. 앞 시나리오가 축소를 기다리는 동안 다른 시나리오가 돌 수 있다.
- 사전 점검의 "A사 HPA가 평소 상태다"는 실패 대신 최대 300초 기다린다(앞 실행 직후 다시 돌릴 때).

**예상:** A사 묶음 약 3~5분.

## 6. ③ 묶음 균형을 맞춘다 (E2E)

**지금:** A사 9개(약 40분), B사 5개(약 17분).

**바꿀 것:** `a-hang-latency`를 B사의 `b-hang-latency`로 옮긴다.

- B사는 서비스 카탈로그에 결제 API(`payment-gateway`, `external_api`)가 의존 대상으로 있다. 그래서 결제 지연의 근거가 로그 신호뿐 아니라 의존 대상 상태(DEGRADED)로도 잡혀 판정이 더 분명하다. A사는 의존 대상에 결제 API가 없어 로그 신호만 근거다.
- 기대값: 처리할 원인 RB-STD-02 → 재시작(B사는 레플리카 1이라 전체 중단 영향을 표시) → 미회복 → RB-STD-04 → 고객 공지.
- ①을 하면 묶음을 다시 짜므로, 이 항목은 ①보다 먼저 하는 빠른 개선이다.

**예상:** A사 묶음 약 4~5분 단축, B사는 그만큼 늘지만 A사보다 짧다.

## 7. ⑤ 알림을 빨리 울리고 빨리 푼다 (관측 설정)

**바꿀 것:**

| 설정 | 지금 | 바꾼 뒤 | 효과 |
|---|---|---|---|
| 알림 규칙 `rate` 창 | `[1m]` | `[30s]` | 이상이 지표에 빨리 드러나고 빨리 빠진다 |
| 알림 규칙 `for` | `30s` | `15s` | 울리기까지 15초 줄어든다 |
| Alertmanager `group_wait` | `10s` | `5s` | 첫 알림이 5초 빨리 온다 |
| Alertmanager `group_interval` | `1m` | `20s` | 해제 알림이 빨리 와서 인시던트가 빨리 닫힌다 (앞 인시던트 닫힘 대기가 줄어든다) |

- 스크랩 주기는 5초라 30초 창에도 점이 6개 있다.
- Agent의 진단 지표(최근 1분)와 회복 확인(15초 창, `signals.VERIFY_WINDOW`)은 그대로 둔다. 판정 기준이 바뀌지 않게 하기 위해서다.
- 운영에서의 근거: 데모 환경은 장애를 바로 넣고 바로 빼므로 1분 창이 길다. 실제 고객사에 적용할 때는 고객사별로 정하는 값이다. `infra/prometheus/alert-rules.yml`에 그렇게 적는다.
- 위험: 짧은 창은 잠깐의 튐에도 울린다. `no-issue`와 `question` 시나리오(장애가 없을 때)가 그대로 통과하는지, 정리 후 다시 울리지 않는지 E2E로 확인한다.

**예상:** 시나리오당 약 20~40초(울리기 + 닫히기), A사 묶음 약 4분.

## 8. ① 고객사마다 장애용 서비스를 늘리고 서비스별로 동시에 돌린다

### 8.1 서비스

전체 시간은 A사 묶음이 정하므로 A사를 3개로, B사를 2개로 늘린다(Docker 메모리 10GB 기준, 8.4).

| 고객사 | 서비스 | 지금/추가 | 구성 |
|---|---|---|---|
| A사 (GITOPS) | `order-api` | 지금 | HPA 2~3, 안전 상한 4 |
| A사 | `cart-api` | 추가 | HPA 2~3, 안전 상한 4, DB(`postgres`) 의존 |
| A사 | `inventory-api` | 추가 | 레플리카 2 고정(HPA 없음), DB(`postgres`) 의존 |
| B사 (DIRECT) | `reservation-api` | 지금 | 레플리카 1, 결제 API 의존 |
| B사 | `checkin-api` | 추가 | 레플리카 1, 결제 API 의존 |

- 모두 같은 데모 앱 이미지다. A사 서비스는 A사 설정 저장소에 서비스마다 파일을 둔다(`cart-api.yaml`, `hpa-cart-api.yaml`, `inventory-api.yaml`).
- 서비스 카탈로그(V20): `services`, `service_dependencies`, 관측 정의(`request_metric`, `log_selector`, `log_signals`). 값은 기존 서비스와 같고 이름만 다르다. `inventory-api`는 HPA가 없으므로 확장 계산값(`autoscaling`)이 없다.
- 매니페스트: Deployment·Service(NodePort 30083 `cart-api`, 30084 `inventory-api`, 30085 `checkin-api`). 의존 장애 ConfigMap 마운트도 같다.
- 부하 발생기: `traffic` 네임스페이스에 HPA가 있는 서비스와 B사 서비스마다 하나(`loadgen-tenant-a-cart-api`, `loadgen-tenant-b-checkin-api`).
- 알림 규칙은 이미 `(tenant, service)`로 묶여 있다. 바꿀 것이 없다.

### 8.2 서비스별로 나눠야 하는 곳

같은 고객사의 두 서비스에 동시에 장애를 넣으려면, 지금 고객사 단위인 다음 값을 서비스 단위로 바꿔야 한다.

| 위치 | 지금 | 바꿀 것 | 이유 |
|---|---|---|---|
| backend `GitOpsRepository.recentCommits` | 고객사 경로(`gitops/tenants/tenant-a`)의 최근 커밋 | 서비스 매니페스트 파일 경로로 거른다 | `cart-api`의 풀 축소 커밋이 `order-api`의 "직전 변경"으로 잡혀 RB-STD-01 판단이 틀어진다 |
| backend 롤백 PR | 고객사 경로의 직전 커밋을 되돌린다 | 그 서비스 파일을 바꾼 직전 커밋을 되돌린다 | 다른 서비스의 커밋을 되돌릴 수 있다 |
| `scripts/inject.py` | `SERVICE = {tenant: 서비스}` 하나 | `inject.sh tenant-a cart-api hang`처럼 서비스를 받는다. 풀 파일·HPA 파일·부하 발생기도 서비스마다 | |
| `scripts/e2e.py` | 묶음 = 고객사, `wait_for_closed_incidents(tenant)`, `_cleared_at[tenant]` | 묶음 = (고객사, 서비스), 대기와 간격도 서비스마다 | |
| `check_no_app_action` | 고객사의 감사 로그 | 서비스로 거른다 | 다른 서비스 묶음의 조치가 잡힌다 |
| `check_one_incident` | 고객사의 인시던트 | 서비스로 거른다 | |
| `reject_stray_tasks` | 고객사의 작업 | 서비스로 거른다 | 다른 묶음의 정상 작업을 거절한다 |
| 사전 점검 | 서비스 2개 | 4개 | |

### 8.3 묶음

| 묶음 | 서비스 | 시나리오 | 예상 (②④⑤ 반영 후) |
|---|---|---|---|
| 공통 | - | `isolation` `question` `no-issue` `documents` | 약 0.5분 |
| A1 | `order-api` | `a-surge` `a-surge-max` `guard` | 약 10~11분 |
| A2 | `cart-api` | `a-pool` `a-dbslow-backlog` | 약 9~10분 |
| A3 | `inventory-api` | `a-hang` `a-latency` `a-dbslow` | 약 6분 |
| B1 | `reservation-api` | `b-hang` `b-pool` `b-surge` `b-pool-dbslow` | 약 9~10분 |
| B2 | `checkin-api` | `b-latency` `b-hang-latency` | 약 6분 |

- HPA 최대치 PR이 필요한 시나리오는 HPA가 있는 `order-api`(`a-surge*`)와 `cart-api`(`a-dbslow-backlog`)에 나눈다. 롤백 PR(`a-pool`)은 `cart-api`에 둔다. `guard`는 HPA 서비스(`order-api`)를 대상으로 한다.
- `a-hang`의 "순차 재시작·무중단" 판정은 레플리카 2인 `inventory-api`에서도 그대로다.
- 동시에 도는 작업은 최대 5개(A1, A2, A3, B1, B2)다. agent 실행 슬롯을 `AGENT_WORKERS=5`로 올린다. 남은 작업이 있으면 Gold(A사)가 먼저 받는다.
- A사 묶음 세 개가 같은 임시 브랜치에 커밋·PR 병합을 한다. 다른 파일이라 충돌하지 않지만, ArgoCD가 한 Application으로 동기화하므로 동기화 완료 알림(`CHANGE_FINISHED`)이 서비스의 PR과 맞는지 확인한다.
- 서비스를 더 늘려도 전체는 약 7~8분 아래로 내려가지 않는다. 가장 긴 시나리오(`a-surge`, `a-dbslow-backlog`: PR 병합·동기화·회복 확인)가 하나에 5~6분이다.

### 8.4 자원

- Docker 메모리를 10GB로 올렸다(2026-10-23). 늘리기 전 실측: compose 스택 약 2.4GB, kind 노드 약 2.6GB(고객사 파드 3개 포함), 합계 약 5.0GB.
- 더해지는 파드: A사 `cart-api` 2~4개, `inventory-api` 2개, B사 `checkin-api` 1개, 부하 발생기 2개. 데모 앱 파드는 평소 약 250Mi, 부하 중 약 340Mi(상한 512Mi)라 약 +2.5~3GB, 합계 약 8GB로 본다.
- 1단계(8.1)를 띄운 뒤 부하 시나리오 두 개(`a-surge`, `a-dbslow-backlog`)를 동시에 돌려 메모리·CPU를 잰다. OOMKilled가 나거나 HPA가 CPU 부족으로 늘지 못하면, 부하 시나리오가 같은 시각에 돌지 않게 하는 규칙을 e2e.py에 둔다(부하 발생기를 켜는 시나리오끼리 잠금).

**예상:** 전체 약 10~12분.

## 9. 결정할 점

1. **⑤ 알림 설정을 데모 환경 전체에 적용할까**
   - (추천) 적용한다. E2E만 다른 값을 쓰면 데모와 E2E의 동작이 달라진다. 실제 고객사에 적용할 값은 고객사별로 정한다고 문서에 적는다.
   - 다른 안: E2E를 돌릴 때만 바꾼다.
2. **① 더할 서비스 이름**
   - (추천) A사 `cart-api`·`inventory-api`, B사 `checkin-api`. 기존 서비스(주문, 예약)와 같은 업무 흐름에 있는 이름이다. 서비스 수는 A사 3개, B사 2개로 정했다(Docker 메모리 10GB, 8.4).
3. **① 범위**
   - (추천) 이번에 한다. ②③④⑤만으로는 약 25분이고, 10분대는 ①이 있어야 한다.
   - 다른 안: ②③④⑤⑥만 하고 ①은 다음으로 미룬다.
4. **③을 ① 전에 할까**
   - (추천) 한다. 작고 바로 효과가 있으며, ①에서도 B2 묶음에 그대로 쓴다.

## 10. 단계

| 단계 | 항목 | 확인 |
|---|---|---|
| 1 | ⑥ `--lane`, testing.md 표 | `--lane tenant-b` 실행 |
| 2 | ② 직전 인시던트로 로그 창 자르기, `QUIET_SECONDS` 제거 | agent 단위 테스트, A사 묶음 E2E, 시간 기록 |
| 3 | ④ 필요한 곳에서만 해제 대기 | A사 묶음 E2E, 시간 기록 |
| 4 | ③ `b-hang-latency` | B사 묶음 E2E |
| 5 | ⑤ 알림 설정 | 전체 E2E, 시간 기록 |
| 6 | ① 서비스 추가(V20, 매니페스트, 부하 발생기), 서비스별 커밋·롤백(backend), `inject.py`·`e2e.py` 서비스 묶음, `AGENT_WORKERS=5` | backend 단위 테스트, 자원 측정, **전체 E2E 18개**, 시간 기록 |

테스트는 작업을 모두 끝낸 뒤 마지막에 한 번에 돌린다. 시간 기록은 각 단계 뒤 E2E에서 재지 않고, 마지막 전체 E2E의 묶음별 시간과 2장의 표를 비교한다.

## 11. 범위 밖

- LLM 호출 시간 줄이기 (모델·프롬프트 변경)
- 같은 서비스 안에서 시나리오를 동시에 돌리기 (증상이 섞인다)
- 시나리오를 빼거나 판정을 느슨하게 하기

## 12. 구현하면서 정한 것

- 의존 장애 ConfigMap은 서비스마다 둔다(`dependency-faults-<서비스>`). 고객사에 하나면 `cart-api`의 DB 지연이 `order-api`의 새 파드에도 옮겨 간다.
- `cart-api`의 HPA는 별도 파일이 아니라 `hpa.yaml`의 두 번째 문서다. backend와 `inject.py`가 HPA 이름으로 그 HPA의 `maxReplicas`만 고친다.
- Alertmanager 묶음 기준에 `service`를 더했다(`[tenant, service, alertname]`). 같은 고객사의 다른 서비스에서 같은 알림이 울리면 앞 묶음에 들어가 `group_interval`만큼 늦게 왔다.
- 서비스 묶음이 같은 임시 브랜치에 동시에 커밋하면 GitHub가 409로 거절할 수 있다. `inject.py`가 파일을 다시 읽어 최대 4번 커밋한다.
- `inject.sh <고객사> clear`는 `--service`가 없으면 그 고객사의 모든 서비스를 정리한다. E2E는 시나리오의 서비스만 정리한다.
- 예전 키 `a-hang-latency`는 `b-hang-latency`로 바뀌었다(`ALIASES`).
- **Loki4j 로그 플러시 주기 단축**: 알림 가속(`for: 15s`)으로 인시던트 생성이 빨라져 데모 앱의 loki4j 기본 버퍼(60초 타임아웃)가 에이전트 진단보다 늦게 flush되는 문제가 발생했다. `logback-spring.xml`에 `<batch><maxItems>50</maxItems><timeoutMs>1000</timeoutMs></batch>`를 설정해 1초 안에 Loki로 로그가 전송되도록 단축했다.
- **해결 인시던트 조회 크기 확장**: 누적된 해결 인시던트 목록 JSON이 4KB를 초과하여 `ToolBox.call`에서 잘리고 JSON 파싱 실패로 `previousResolvedAt`이 `None`이 되던 문제를 수정했다. `load_context`의 `business_list_incidents` 호출 시 `max_chars=200_000`을 넘겨 정상 파싱되도록 하고, 타입 검사를 안전하게 보강했다.
- **부하 발생기 없는 서비스의 정리 예외 방지**: `inventory-api`는 HPA와 부하 발생기가 없는 서비스다. `scripts/inject.py`가 부하 발생기가 있는 서비스 목록(`LOADGEN_SERVICES`)을 관리하고, 없는 서비스에 대해 `set_surge`를 건너뛰며 개별 복원 단계를 예외 처리로 보호했다.
- **서드파티 외부 의존(RB-STD-04) 조치 가드**: `action_guards.py`에서 `category == "DEPENDENCY"` 에스컬레이션을 허용하는 대상을 `RB-STD-05`(내부 팀 소유 DB)로 한정하여, 서드파티 소유 외부 API(`payment-gateway`)는 실패하는 에스컬레이션 대신 런북에 따라 고객 공지(`business_send_customer_notice`)를 직접 제안하도록 일관성을 확보했다.

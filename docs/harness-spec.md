# Harness 명세

PDF 표 6-3의 권장값을 기준으로 둔 값과, 팀이 바꾼 값과 이유를 기록한다. 코드는 `server/python/agent/src/agent/harness.py`에 있다.

## 현재 값

| 구성 요소 | 항목 | 값 | PDF 권장값 | 비고 |
|---|---|---|---|---|
| Agent Loop | 모델 호출 최대 턴 | 8 | 8 | |
| Agent Loop | 작업 실행 시간 | 120초 | 120초 | 승인·PR 반영 대기, 회복 확인 대기(20초, 재측정 20초)는 빼고 구간별로 잰다 (`agent.task_budget`) |
| Agent Loop | 회복 확인 | 조치 20초 뒤 15초 창, 기준 안이면 20초 뒤 한 번 더 | — | 둘 다 알림 기준(에러율 0.2, p95 1초) 안이어야 회복 (`harness.VERIFY_*_SECONDS`, `signals.within_thresholds`) |
| Agent Loop | 같은 도구 호출 | Node마다 3회 | 3회 | |
| Tools | 도구 결과 길이 | 4,000자 | 길이 제한 | |
| Context | 대화 예산 | 24,000자 | Token 예산 | 넘으면 오래된 도구 결과를 줄인다. 모델 요약은 TODO |
| Recovery | 429·5xx 재시도 | 2회 | 2회 | 이후 대체 모델(`FALLBACK_MODEL`) |
| Recovery | Kafka 작업 재시도 | 3회 | — | 등급별 재시도 토픽(`tasks.retry.gold`·`silver`, 2ⁿ초 뒤), 이후 `tasks.dlt`, 작업은 FAILED |
| Recovery | 모델 호출 타임아웃 | 30초 | — | 타임아웃도 재시도 대상이라 한 턴이 최대 90초까지 걸린다 |
| Agent Loop | 추론 강도 | low (propose_action만 medium) | — | `LLM_REASONING_EFFORT`. 비우면 모델 기본값. propose는 `harness.PROPOSE_REASONING_EFFORT` |
| Agent Loop | 도구 호출 강제 | propose_action 전체, diagnose 첫 턴 | — | diagnose는 `DIAGNOSE_FORCE_FIRST_TOOL`(기본 true). 진단 도구는 조회 전용이라 강제해도 부작용이 없다 |
| Guardrails | 조치 돌려보내기 | 최대 2번 | — | 인용 조항과 맞지 않는 조치(직전 변경이 있으면 RB-STD-01 재시작 불가)와 `supportedActions`에 없는 조치를 이유와 함께 돌려보낸다 (`graph.PROPOSE_SEND_BACKS`) |
| Guardrails | 고객 공지 문안 | 규칙 검사 → 고쳐 쓰기 1번 → 기본 문안 | — | `prompts/notice.md`, `agent/customer_notice.py`. 발송 전 MSP 관리자 승인은 그대로 |

## 변경 이력

| 날짜 | 항목 | 이전 → 이후 | 이유 | 평가 결과 |
|---|---|---|---|---|
| 2026-10-19 | 추론 강도 | 모델 기본값(medium) → low | 시나리오 A 진단 마지막 턴에서 gpt-5-mini 호출이 30초 타임아웃을 3번 넘겨 120초 한도로 FAILED. 단독 측정에서 low가 응답 시간 절반(10.5초 → 5.4초) | 시나리오 A 재실행: 120초 안에 끝까지 진행(108.8초) |
| 2026-10-19 | 회복 확인 대기, rate 창 | 10초·창 지정 없음 → 20초·`[15s]` | 모델이 `[5m]` 창으로 조회해 조치 전 에러가 섞여 회복을 놓침(demo-app은 `fault: NONE`인데 NOT_RECOVERED) | 시나리오 A 재실행: 진단·런북 검색은 정확해짐(Hikari 로그, RB-A-01). 회복 확인은 조치가 안 나와 미검증 |
| 2026-10-19 | propose_action 추론 강도 | low → medium | low에서 조치 도구를 호출하지 않고 권고문만 씀(2/2). 같은 입력으로 medium은 3/3 `adjust_pool(20)` 호출, 8~17초 | 시나리오 A 재실행: 진단 최종 답이 2,500자(출력 1,700~2,100 token, 약 29초)라 30초 타임아웃 재시도로 FAILED |
| 2026-10-19 | 진단 최종 답 길이 | 제한 없음 → 형식만, 10줄 이내 (`prompts/diagnose.md`) | 위 FAILED. 출력 길이가 응답 시간을 좌우한다 | 시나리오 A 재실행: 진단은 정답(Hikari, RB-A-01), propose가 또 도구 미호출 |
| 2026-10-19 | propose_action 도구 강제 | 선택 → `tool_choice="required"` | 같은 입력 5회 중 1회(37초) 권고문만 씀. 조치 도구 2개 중 하나를 반드시 고르게 하고, 할 조치가 없으면 공지를 고른다. "도구 없이 이유만 답한다" 경로는 없어짐 | 시나리오 A 재실행 |
| 2026-10-19 | 같은 도구 호출 한도 범위 | 작업 전체 3회 → Node마다 3회 | 시나리오 B(파드 재시작)에서 diagnose가 `grafana_query_prometheus`를 3번 쓰자 verify가 메트릭을 한 번도 조회하지 못해 NOT_RECOVERED. 실제로는 재시작 7초 뒤 파드가 Ready였다 | 시나리오 B 재실행 |
| 2026-10-19 | 회복 확인 대기의 시간 계산 | 실행 시간에 포함 → 제외 (`Context.settle`) | 위 실행에서 재진단 전까지 111.7초를 써서 회복 확인 중 120초를 넘길 상황. 20초 대기는 Agent가 일하는 시간이 아니라 시스템 반영을 기다리는 시간이다 | 시나리오 B 재실행 |
| 2026-10-19 | 회복 확인 에러율 쿼리 | `sum(error)/sum(total)` → `(sum(error) or vector(0))/sum(total)` (`prompts/verify.md`) | 시나리오 A에서 롤백 후 새 파드에 에러가 하나도 없어 에러 시계열이 없었고, 쿼리 결과가 비어 모델이 NOT_RECOVERED로 판단해 재진단함 | 시나리오 A 재실행 |
| 2026-10-19 | 조치 제안 필수 인자 확인 | 없음 → `action`·`service`가 없으면 승인 요청을 만들지 않음 (`graph.propose_action`) | 위 재진단 뒤 propose_action이 인자 없는 `execute_runbook_action`을 승인 요청으로 올림 | 시나리오 A 재실행 |
| 2026-10-19 | 회복 확인 첫 호출 도구 강제 | 선택 → `tool_choice="required"` (첫 턴만) | 시나리오 A 재실행에서 PR 병합·동기화 후 verify가 메트릭을 한 번도 조회하지 않고 NOT_RECOVERED로 답함. 그 결과 재진단 → 두 번째 롤백 PR이 수정 커밋을 되돌려 장애가 재발 | 시나리오 A 재실행 |
| 2026-10-19 | 회복 확인 쿼리 | 모델이 PromQL 작성(첫 호출 강제) → 코드가 표준 쿼리로 조회, 모델은 수치로 판단만 (`graph.VERIFY_QUERIES`) | 첫 호출을 강제해도 시나리오 B에서 모델이 없는 메트릭 이름(`reservation_api_requests_seconds_count`)과 깨진 이스케이프로 조회해 두 쿼리 모두 실패, NOT_RECOVERED. 메트릭이 비면 10초 더 기다려 한 번 다시 조회한다 | 시나리오 B 재실행 |
| 2026-10-19 | 보고서 입력 | 회복 여부만 → 회복 확인 결론·수치(`verification`), 조치 결과, PR 정보 추가 (`prompts/report.md`) | 시나리오 A 보고서가 "확인 필요", "로그 미제공"으로 채워지고, 회복된 뒤에도 공지 문안이 "동기화되면 확인하겠다"고 씀 | 시나리오 B 재실행 |
| 2026-10-19 | diagnose 첫 턴 도구 강제 | 선택 → 첫 턴만 `tool_choice="required"`, 성공한 도구 결과가 없으면 `원인 미확정` | 첫 턴에 조사 계획만 쓰고 도구 없이 끝나 계획 글이 런북 검색어와 조치 제안의 입력이 됨 ([기능별-변경사항.md](기능별-변경사항.md) 8장) | diagnose Node 단독 3회(A사, 최근 2시간 안에 DB 커넥션 풀 장애 구간이 있고 측정 시점에는 회복된 상태): 첫 턴 도구 호출 3/3, 원인 정답(HikariPool) 3/3, 2~4턴, 11~19초. 계획 글은 3회 모두 비어 있음. 시나리오 A·B 5회는 미실행 |
| 2026-10-19 | 회복 판정 해석 | 첫 줄이 `RECOVERED`로 시작해야 회복 → 답 어디서든 `RECOVERED`/`NOT_RECOVERED`를 찾고, 없으면 기준값으로 판정 (`graph.verify`) | E2E 시나리오 A에서 수치는 정상(에러율 0.00)이고 모델도 "회복 여부 확인: RECOVERED"라고 썼지만 형식이 달라 미회복으로 처리, 두 번째 롤백 제안. 롤백 멱등성으로 PR은 만들어지지 않음 | `scripts/e2e.py a` |
| 2026-10-19 | 진단 로그 조회 | 모델이 선택 → 진단 시작 때 코드가 최근 10분 ERROR·WARN 로그를 조회해 반복 메시지별로 묶어 전달 (`graph._recent_log_patterns`) | E2E 시나리오 B2에서 모델이 로그를 보지 않아 `payment API timeout`을 놓치고, 외부 원인인데 파드 재시작을 제안(RB-B-01 위반). 스크립트가 거절한 뒤 공지를 제안 | `scripts/e2e.py b2` |
| 2026-10-20 | 진단 로그 조회 구간 | 최근 10분 → 인시던트 시작 2분 전부터, 최대 10분 (`graph._log_window_start`) | E2E에서 B(파드 멈춤) 직후 B2(결제 지연)를 진단할 때 10분 창에 남은 B의 `worker pool saturated` 로그 92건을 보고 재시작을 제안 | `scripts/e2e.py b b2` |
| 2026-10-20 | 조치 제안 순서 | 규칙 나열 → ① 로그로 장애 형태 → ② 조항의 조치 → ③ `supportedActions`·`safeMaxReplicas` 확인 → ④ 조치·공지·에스컬레이션 (`prompts/propose.md`). 의존 대상은 그 대상의 오류 로그가 있을 때만 원인으로 본다 | 에스컬레이션 규칙을 추가한 뒤 B에서 서비스 목록의 결제 대행사(서드파티)만 보고 재시작 대신 공지를 제안 | `scripts/e2e.py b` |
| 2026-10-20 | 런북 검색 (`retrieve_runbook`) | 검색 1번(고객사 문서) → 표준 런북 2 + 고객사 문서 2 + 계약 허용 조치 1 (`scope`로 범위 지정). 이 Node의 같은 도구 한도는 방문마다 새로 센다 | 표준 런북(`_common`)을 추가하자 한 번 검색으로는 표준 조항이 상위를 채워 서비스 런북·계약 조항이 밀려남. 계약 허용 조치(제3조)는 증상 문장과 달라 증상으로 검색되지 않음. 재진단 뒤 다시 검색할 때 같은 도구 한도(3회)에 걸림 | 장애 5종 × 고객사 2곳 검색: 표준 조항 1순위 10/10, 계약 제3조 10/10, 다른 고객사 문서 0건 |
| 2026-10-20 | 진단 기본 지표 | 모델이 선택 → 진단 시작 때 코드가 최근 1분 에러율·p95·처리량을 조회해 함께 전달 (`graph.DIAGNOSE_QUERIES`). 같은 도구 한도에는 넣지 않는다 | B사 트래픽 급증에서 모델이 에러율을 보지 않고 `request queue backlog`(처리 슬롯 4/4)를 작업 스레드 고갈로 읽어 RB-STD-02(재시작)를 제안 | `probe b-surge` (E2E `b-surge` 예정) |
| 2026-10-20 | 대체 조치 금지 | 조항의 조치를 할 수 없으면 `supportedActions`의 다른 조치로 대신하지 않고 에스컬레이션 (`prompts/propose.md`, RB-STD-03) | B사 급증에서 확장이 계약상 불가하자 효과 없는 재시작을 "백로그 완화"로 제안 | 같음 |
| 2026-10-20 | 조항-조치 검사 | 없음 → 제안 이유가 인용한 공통 런북 조항(RB-STD-0x)과 고른 조치가 맞지 않으면 거부 이유를 도구 결과로 돌려주고 한 번 다시 고르게 한다 (`graph.CLAUSE_ACTIONS`, `_clause_mismatch`). 에스컬레이션·공지는 검사하지 않는다 | 판정표를 준 뒤 B사 급증을 RB-STD-03으로 맞게 판단하고도 "큐를 비운다"며 재시작을 제안 (gpt-5-mini, 추론 low) | `probe b-surge` |
| 2026-10-20 | 진단 변경 이력 | 모델이 선택 → 진단 시작 때 코드가 인시던트 서비스의 최근 변경을 조회해 인시던트 시작 시각과 함께 전달. 같은 도구 한도에 넣지 않는다 | 기본 지표를 미리 넘긴 뒤 시나리오 A에서 모델이 변경 이력을 조회하지 않고 "직전 변경 없음"이라고 단정해 롤백 대신 재시작을 제안 (RB-STD-01 → 02 오판) | `scripts/e2e.py a` |
| 2026-10-20 | 에스컬레이션 `required_action` 값 | `restart_pods`·`scale_deployment`·`rollback_deployment` → `adjust_autoscaler` 추가 (business-mcp `escalate_incident`) | E2E `a-surge-max`에서 최대치를 안전 상한 4로 올린 뒤에도 미회복이라 `ACTION_BLOCKED`, `required_action="adjust_autoscaler"`로 에스컬레이션했지만 MCP 인자 검증에서 거부돼 대기열에 들어가지 않음. 실패 뒤 고객 공지 문안에 내부 오류("형식 오류로 실패")와 다른 언어 단어가 섞임 | `scripts/e2e.py a-surge-max` |
| 2026-10-20 | 안전 상한에 막힌 확장의 `blocked_by` | 규칙만 → "최대치가 이미 `safeMaxReplicas`면 `STRUCTURE`, 계약 상한은 더 높아 `CONTRACT`가 아니다"를 명시 (`prompts/propose.md`) | 위 실행에서 모델이 `blocked_by="CONTRACT"`로 적음 | 같음 |
| 2026-10-20 | RB-STD-01의 재시작 허용 | 언제나 → 직전 변경이 없을 때만. 진단 때 코드가 인시던트 시작 전 15분 안의 변경 여부(`signals.recent_change`)를 계산하고, 있으면 RB-STD-01 인용 재시작을 거부해 다시 고르게 한다 (`graph._recent_change`, `_clause_mismatch`) | E2E `b-pool`에서 직전 배포(풀 5)가 있는데 롤백이 계약상 불가하자 "RB-STD-01 1차 조치로 재시작"을 제안. 조항-조치 검사가 RB-STD-01의 재시작 예외를 그대로 허용함 | `scripts/e2e.py b-pool`: 재시작 제안 없이 `ACTION_BLOCKED rollback_deployment CONTRACT`, B사 운영 승인 담당 |
| 2026-10-20 | 재진단 신호 | 첫 진단 값 재사용 → 재진단 때 코드가 로그 반복 메시지·로그 신호·기본 지표를 다시 조회해 전달 (`graph.diagnose`) | `a-surge-max` 재실행에서 첫 진단 시점에는 `request queue backlog` 로그가 아직 Loki에 없어 신호 0건이었고, 재진단에서도 그 0건을 써서 판정표에 맞는 행이 없어 `CAUSE_UNKNOWN`으로 에스컬레이션. Loki에는 그 뒤 분당 168~220건이 있었다 | `scripts/e2e.py a-surge-max` |
| 2026-10-20 | 회복 확인 재측정 | 한 번 → 첫 값이 기준 안이면 20초 뒤 다시 재서 둘 다 기준 안이어야 회복 (`graph.verify`, `prompts/verify.md`) | `a-surge-max`(+400건/초)에서 최대치 4 동기화 직후 15초 창 p95 0.60초를 보고 RECOVERED. 직접 재 보니 파드 4개의 안정 상태 p95는 약 3초이고, 새 파드가 Ready가 된 직후 15초만 0.98초로 내려갔다(빈 파드가 대기 요청을 받음) | 측정: 400건/초, HPA 최대 4, 15초 간격 16회 p95 5.4→1.0→2.8~3.8초 |
| 2026-10-20 | 고객 공지 문안 검사 | 지시문만 → 공지 규칙을 `prompts/notice.md`로 분리하고, 코드가 문안을 검사한다(`customer_notice.problems`): 한글·영문 외 문자, 내부 처리·식별자·지표 용어(에스컬레이션, 승인, PR, `tenant-`, p95 등), 지시문을 옮긴 말과 머리말("초안", "발송해 주세요"), 목록 형식, 400자 초과, 회복 전인데 구체적인 다음 안내 시각 없음. 어기면 한 번 고쳐 쓰고, 그래도 어기면 상태값으로 만든 기본 문안을 쓴다. 보고서의 공지와 조치로 고른 공지 모두 거친다 | `a-surge-max`에서 공지에 "에스컬레이션 요청은 형식 오류로 실패"와 다른 언어 단어(अभी)가 섞임. 확인해 보니 처음부터 구성한 전체 E2E의 공지 11건 중 9건이 목록 형식, 내부 절차("발송 대기 상태", "PR 병합"), 머리말("검토 후 발송해 주세요")을 담았다 | 같은 11건을 실제 모델로 다시 처리: 고쳐 씀 9, 기본 문안 2, 규칙 위반 0 |
| 2026-10-20 | 배포 이력의 배포 시각 | ReplicaSet 생성 시각만 → 현재 리비전에 실제 배포 시각(`deployedAt`, Deployment `Progressing` 상태의 마지막 갱신)을 더하고, 직전 변경 판단(`graph._recent_change`)이 먼저 본다 | `b-pool` 재실행에서 풀 5 배포가 예전 실행의 같은 설정 ReplicaSet을 다시 써서 생성 시각이 1시간 전으로 남았다. 직전 변경 없음으로 판단해 RB-STD-01 재시작 제안이 거부되지 않음 | B사 배포 이력: 리비전 5 생성 05:35, 배포 06:30. `scripts/e2e.py b-pool` |
| 2026-10-20 | 조치 돌려보내기 | 조항 검사 1번 → 조항 검사와 `supportedActions` 검사를 함께, 최대 2번 돌려보내고 거부 이유를 쌓아 넘긴다 (`graph._unsupported_action`, `PROPOSE_SEND_BACKS`) | 위 수정 뒤 `b-pool`에서 재시작이 거부되자 계약상 불가한 `rollback_deployment`를 골랐고, 두 번째 선택은 검사하지 않아 승인 요청으로 올라감(backend가 422로 막으므로 실행은 안 됨) | `scripts/e2e.py b-pool`: P1 → `ACTION_BLOCKED rollback_deployment CONTRACT` → 공지 |
| 2026-10-20 | 장애 주입·해제 대상 | Service(NodePort)로 한 번 → 모든 파드에서 자기 관리 API를 부른다 (`scripts/inject.py set_fault`, `kubectl exec`) | 처음부터 구성한 전체 E2E에서 `a-latency` 해제가 장애를 넣지 않은 다른 파드(A사는 2개)로 가 지연이 남았다. p95 1.9초가 7분 이상 이어져 인시던트가 닫히지 않았고, 다음 `a-dbslow`의 알림이 그 인시던트로 합쳐져 새 작업이 생기지 않음 | 주입 후 두 파드 모두 `PAYMENT_LATENCY`, 해제 후 두 파드 모두 `NONE` |
| 2026-10-20 | 막힌 조치 에스컬레이션 검사 | 없음 → `ACTION_BLOCKED`의 `required_action`이 코드가 신호로 정한 장애 형태(RB-STD-01~03)의 조치와 다르면 돌려보낸다 (`graph._signal_clause`, `_blocked_action_mismatch`) | 같은 실행의 `b-pool`에서 진단은 RB-STD-01(풀 고갈)로 맞히고 에스컬레이션의 필요한 조치를 `scale_deployment`로 적음. B사 승인 담당에게 엉뚱한 조치의 승인을 요청하게 된다 | 단위 테스트. 전체 E2E 재실행 |


## 과거 장애 검색 (2026-10-22)

첫 `diagnose` 진입에서 `retrieve_history` 허용목록으로 `rag_search_documents`를 한 번 호출한다.
검색어는 서비스·질문·현재 로그 신호이며 `scope=tenant`, `doc_type=postmortem`, `top_k=2`로 제한한다.
현재 인시던트의 자동 회고 ID는 검색에서 제외한다. 검색 제한은 8초이며 기존 작업 시간 예산에 포함된다.
검색 결과를 파싱하기 위한 상한은 18,000자이고, 모델에 전달하는 본문은 조각당 1,800자로 제한한다.
재진단에서는 처음 얻은 사례를 재사용한다. 오류·빈 결과이면 기존 진단을 계속한다.

자동 회고는 미검토 AI 보고서임을 매 조각과 프롬프트에 명시한다. 현재 로그·지표와 비교해 관련 있을 때만
참고하고, 기존 조치·계약·구조 검사와 승인 흐름을 유지한다. 인시던트 진단의 런북 검색은 `doc_type=runbook`으로
분리해 회고가 런북을 밀어내지 않게 한다. 과거 사례 출처는 작업의 sources에 함께 저장한다.

검증: Agent의 `test_history.py` 4개와 실제 pgvector를 사용하는 색인·RAG 통합 테스트 통과.
실제 모델의 원인 판정 정확도나 처리 시간 개선은 아직 측정하지 않았다.


### 첨부 분석 경로

`ATTACHMENT` 작업은 `load_context → analyze_attachments → retrieve_runbook → report`로 진행한다.
열린 장애가 없어도 첨부 자료를 분석하며 `propose_action`·승인·변경 도구·공지로 넘어가지 않는다.
첨부 분석 Node는 고정된 Prometheus 조회 3회와 도구 없는 멀티모달 모델 호출만 사용한다.
로그 입력은 파일별 앞뒤 약 16,000자(최대 3개)로 제한하고 줄 번호·생략 표식을 넣는다.
이미지는 별도 내부 API로 받아 모델에 한 번 전달하고 State에는 분석 결과와 파일 메타데이터만 남긴다.
기존 시간 한도 120초와 모델 재시도 정책을 유지한다. 첨부 작업에 한해 MCP 연결이 일부 실패해도
사용 가능한 도구로 진행하며, 조회할 수 없는 현재 지표나 문서는 확인 불가로 표시한다.

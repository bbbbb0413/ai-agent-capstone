# Spec: 코드 리뷰 지적 사항 수정

- 상태: **완료** (2026-10-23). 남은 일은 4장(하지 않는 것)
- 근거: 2026-10-23 전체 구조·코드 품질 리뷰
- 원칙: 단계마다 테스트를 붙이고 따로 커밋한다. 기능 수정과 파일 분리(리팩터링)는 섞지 않는다.

## 1. 공통 원인

실패를 조용히 삼키는 곳이 많아 작업이 RUNNING으로 남고 아무도 다시 집지 않는 경로가 여럿 있다. 1~4단계가 이 경로를 막는다.

## 2. 단계

| # | 내용 | 위치 | 완료 기준 | 상태 |
|---|---|---|---|---|
| 1 | agent → backend 호출의 응답 상태 확인 | `agent/backend.py` | 4xx·5xx면 예외. `result()`는 짧게 재시도. 실패가 로그·재시도 경로로 간다 | 완료 |
| 2 | 반영 대기 재개 전달 실패 시 되돌리기 | `TaskService.finishChange`, `TaskRepository` | `agent.resume` 실패 시 CHANGE_PENDING으로 복원, 다음 동기화 알림·시간 초과 점검이 다시 시도. 단위 테스트 | 완료 |
| 3 | Kafka 체인 예외 시 미커밋 오프셋으로 되감기 | `kafka_worker.py` `_chain_done` | 체인이 예외로 끝나면 커밋 안 된 첫 레코드로 `seek`, 예외는 로그로 남긴다. 단위 테스트 | 완료 |
| 4 | 끝난 작업 상태 덮어쓰기 막기 | `TaskRepository.complete`·`waitForApproval`·`waitForChange`, `TaskService.complete` | DONE·FAILED 작업은 바뀌지 않는다. `complete()`의 작업 갱신과 보고서 저장을 한 트랜잭션으로. DB 테스트 | 완료 |
| 5 | 테스트 빈틈 메우기 | `tests/test_task_outcomes.py` | 리뷰의 뮤테이션 4개가 모두 테스트 실패로 잡힌다 (아래 3장) | 완료 |
| 6 | 재개가 반영되지 않으면 backend에 알리기 | `runner.resume`, `main.py`, backend | `RUNNING_ELSEWHERE`·실패로 결정이 버려지면 승인 카드가 복원되거나 재시도된다 | 완료 |
| 7 | 조치 분기 `default` 제거 | `ChangeService.execute` | `rollback_deployment`를 명시하고 모르는 조치는 예외 | 완료 |
| 8 | 보고서 토큰 묶어 보내기 | `graph.report`, `TaskService.onEvent` | 토큰을 짧은 간격으로 모아 보낸다. TOKEN 이벤트는 상태 UPDATE를 하지 않는다 | 완료 |
| 9 | Kafka 소비자 생존 여부를 health에 반영 | `kafka_worker.consume`, `main.py` | 모든 예외에 재연결하고 로그를 남긴다. 소비 루프가 죽으면 `/health`가 실패 | 완료 |
| 10 | runner 정리 코드가 원래 예외를 가리지 않게 | `runner._drive_claimed` | `finally`의 DB 실패는 로그만 남긴다 | 완료 |
| 11 | `graph.py` 분리 (리팩터링만) | `graph.py` → `signals.py`, `action_guards.py` | 동작 변화 없음. 진단 사전 조회 중복과 RB-STD-01 재시작 제외 중복을 하나로. 기존 테스트 그대로 통과 | 완료 |
| 12 | 조항별 조치 규칙 일치 테스트 | `CLAUSE_ACTIONS`, `runbook-standard.md` | 문서의 RB-STD 조항 목록과 코드가 어긋나면 테스트 실패 | 완료 |

단계마다 agent 단위 테스트(`uv run python -m unittest discover -s tests`)와 backend `./gradlew test`를 돌린다. 모두 끝나면 E2E `--quick`으로 확인한다.

## 3. 5단계에서 잡아야 할 뮤테이션

결과: 넷 모두 이제 테스트가 실패로 잡는다. 12단계 테스트도 `CLAUSE_ACTIONS`에 조치를 더하거나 빼는 변경을 잡는다.

### 구현 메모

- 2단계: 되돌릴 때 `updated_at`은 처음 기다리기 시작한 시각으로 둔다. 새로 고치면 병합 기한 계산이 늦춰진다.
- 3단계: 실패한 레코드부터 다시 읽기 전 `CHAIN_RETRY_SECONDS`(5초)를 기다린다. backend가 계속 죽어 있을 때 빈 루프를 돌지 않게 한다.
- 6단계: backend를 바꾸지 않고 agent가 임대를 기다린다(최대 75초). 끝내 못 잡으면 작업을 FAILED로 끝내 화면에 드러낸다.
- 8단계: 토큰을 0.1초씩 모아 보낸다. backend는 TOKEN 이벤트에 DB를 건드리지 않고 화면에만 중계한다.
- 9단계: compose에는 agent healthcheck가 없다. `/health`가 503을 돌려주는 것으로 운영자가 확인한다.
- 11단계: `graph.py` 997줄 → 784줄. `signals.py`(로그 신호·지표·직전 변경·회복 기준, `observe()`로 진단 사전 조회 중복 제거)와 `action_guards.py`(조항별 조치, 돌려보내기 검사 4개, `fixes()`로 RB-STD-01 재시작 제외 중복 제거, `first_problem()`). 첫 진단의 로그 조회는 지금처럼 모델 한도에 들어가고, 재진단은 들어가지 않는다. 검사 묶음을 보는 테스트를 더했다.

### 뮤테이션 목록

| 바꾼 조건 | 추가할 테스트 |
|---|---|
| `_signal_clause`의 `len(found) == 1` → `if found` | 신호가 두 장애 형태에 맞으면 None |
| `_blocked_action_mismatch`의 RB-STD-01 재시작 제외 제거 | 직전 변경이 있는데 required_action=restart_pods면 거부 |
| RB-STD-03의 외부 API·느린 쿼리 제외 제거 | 대기열 신호와 결제 지연 신호가 함께 있으면 None |
| verify의 다시 잰 값 덮어쓰기 제거 | 모델이 RECOVERED라도 두 번째 측정이 기준을 넘으면 NOT_RECOVERED |

## 4. 하지 않는 것 (문서에 한계로 남긴다)

- 데모 앱 전용 메트릭·로그 문구를 서비스 카탈로그로 옮기는 일. 데모 범위에서는 동작한다. 서비스가 둘 이상이면 회복 확인 수치가 섞이는 한계를 기획안에 적는다.
- 서비스별 내부 토큰, CSRF 토큰. 위협 모델과 SameSite 근거를 문서에 적는다.

# Spec: 대규모 트래픽 대응을 위한 Kafka 발행·소비 신뢰성 및 확장성 보완

- 상태: **구현됨** (2026-10-20, 커밋 `24d8fd5`. 처음부터 구성한 전체 E2E 검증 중). 결정 사항은 12장, 코드 대조 검토는 13장. 같은 날 Kafka를 기본 작업 전달 방식으로 바꿨다
- 작성: 2026-10-20 (개정 1: 결정 사항 반영, 개정 2: 코드 대조 검토 반영)
- 관련: [기획안](../OpsLens-기획안.md) 3.4·8·10장, [Harness 명세](../harness-spec.md)

## 1. 배경: 지금 무엇이 문제인가

현재 `DISPATCH_MODE=kafka` 경로는 비동기 접수(202), SLA 등급별 토픽(`tasks.gold`, `tasks.silver`), 재시도·DLT(`tasks.retry`, `tasks.dlt`), 승인 대기(interrupt) 시점 오프셋 선커밋 구조를 갖추고 있다. 그러나 단일 컨슈머·소규모 동시성(MVP)을 전제로 구현되어 있어, **트래픽 급증이나 컨슈머 수평 확장(Scale-out) 시 설계 의도가 코드 레벨에서 깨지는 7가지 결함**이 있다.

| # | 영역 | 현재 동작 | 원인 (코드) | 대규모 환경에서의 문제 |
|---|---|---|---|---|
| 1 | **발행 정합성** | DB에 `QUEUED` 저장 후 `kafka.send()`를 호출하고 기다리지 않음 (Dual-Write) | `TaskService.create`, `KafkaTaskDispatcher.dispatch` | `kafka.send()`는 비동기(`CompletableFuture`)라 브로커 전송 실패가 `try-catch`에 잡히지 않는다. DB 커밋 직후 서버가 죽거나 브로커가 일시 중단되면 작업이 영원히 `QUEUED`로 남는다 (좀비 작업) |
| 2 | **SLA 우선순위** | 한 컨슈머가 Gold·Silver·Retry를 모두 구독하고 `pause()`/`resume()`과 `sorted()`로 제어 | `kafka_worker.consume` (L41~48) | `sorted()`를 해도 `asyncio.gather()`로 동시에 실행되어 Gold와 Silver가 자원을 경쟁한다. 컨슈머를 여러 대로 늘리면 Silver 파티션만 배정된 파드는 Gold 적체를 몰라 SLA 우선순위가 깨진다 |
| 3 | **테넌트 순서 보장** | 배치에서 꺼낸 모든 레코드를 `asyncio.gather()`로 병렬 실행 | `kafka_worker.consume` (L51) | Producer는 `tenant_id`를 키로 넣어 같은 파티션에 모았지만, Consumer가 한 배치 안의 같은 고객사 작업을 동시에 실행해 순서 보장이 깨진다 |
| 4 | **재시도 지연 (HOL Blocking)** | 메인 컨슈머 루프 안에서 `RETRY` 레코드에 `await asyncio.sleep(2 ** attempts)` 수행 | `kafka_worker._handle` (L62~63) | 배치에 `RETRY` 작업이 1개만 섞여도 대기 시간 + 실행 시간이 끝날 때까지 `gather()`와 `commit()`, 다음 `getmany()`가 막혀 새로 들어온 `GOLD` 작업까지 지연된다. 또한 재시도 시 Gold·Silver 구분이 사라진다 |
| 5 | **장시간 실행과 커밋 지연** | 배치 전체가 끝난 뒤에야 `await consumer.commit()` 일괄 호출 | `kafka_worker.consume` (L51~52), `runner._drive` | 작업 하나가 최대 180초(`TASK_SECONDS` 120초 + 회복 대기 60초) 걸리는 동안 같은 배치에서 5초 만에 끝난 작업들의 오프셋도 커밋되지 않는다. 리밸런싱·재배포 시 완료된 작업이 재소비된다 |
| 6 | **중복·복구 사각지대** | `snapshot.values`가 있으면 `retry=True`가 아닌 한 무조건 `"DUPLICATE"`로 건너뜀 | `runner.run` (L62~68) | 파드 1개가 재시작되면 `task_budget.running`이 남아 시작 때 `recover()`가 이어 간다(지금도 동작). 그러나 파드가 여러 개이고 죽은 파드가 다시 뜨지 않으면, 파티션을 넘겨받은 파드가 원본 토픽에서 다시 읽고 `"DUPLICATE"`로 버린 뒤 커밋해 작업이 유실된다 |
| 7 | **동시성·파티션 한계** | HTTP `/resume`만 `_slots` 세마포어를 쓰고 Kafka 워커는 별도 동작. 토픽 파티션 3개 하드코딩 | `main.py` (L29, L87), `KafkaTaskDispatcher.Topics` | Kafka 소비(`workers`개)와 승인 재개(`/resume`, `workers`개)가 동시에 몰리면 최대 `2 × workers`개가 돌아 DB 풀·모델 한도를 넘는다. 파티션 수가 코드에 고정되어 환경별 확장이 불가능하다 |

## 2. 가정

1. **새로운 외부 인프라(Redis, Debezium 등)는 추가하지 않는다.** 현재 스택(PostgreSQL 16, Apache Kafka KRaft, Spring Boot, Python FastAPI + `aiokafka`) 안에서 해결한다.
2. **작업 생성과 발행 대기열 기록은 Transactional Outbox 패턴으로 묶는다** (결정 1). 같은 DB 트랜잭션 안에서 `ai_tasks`와 `task_outbox`를 함께 기록하고, 즉시 발행 시도 + 폴링 릴레이로 브로커 일시 장애에도 유실 없이 전달한다.
3. **같은 고객사(`tenant_id`)의 작업은 발행 순서대로 처리하되, 서로 다른 고객사 작업은 병렬로 처리한다.**
4. **컨슈머 역할(`AGENT_CONSUMER_ROLE`)을 환경변수로 나눌 수 있게 한다** (결정 2). 기본 `docker-compose.yml`에서는 파드 1개가 `AGENT_CONSUMER_ROLE=all`로 Gold·Silver 루프를 모두 돌리고, 운영·스케일아웃 시에는 `gold`/`silver` 파드로 분리 배포할 수 있게 한다.
5. **토픽 기본 파티션 수는 지금처럼 `3`으로 유지하되 환경변수(`KAFKA_PARTITIONS`)로만 열어둔다** (결정 3).

## 3. 목표와 범위

### 하는 것

- **Transactional Outbox 도입 (`V12__task_outbox.sql`)**: `ai_tasks` 생성과 `task_outbox` 저장을 단일 DB 트랜잭션으로 묶고, Outbox Relay(즉시 전송 + 주기적 재시도 스케줄러) 및 브로커 설정(`acks=all`, 멱등 프로듀서)으로 유실·좀비 작업을 방지
- **SLA 등급별·재시도 컨슈머 분리**: 재시도 토픽을 `tasks.retry.gold`, `tasks.retry.silver`로 나누고 Gold/Silver 소비 루프를 분리해 재시도 대기와 Silver 작업이 Gold 처리를 막지 않도록 격리
- **키(`tenant_id`) 단위 직렬 + 키 간 병렬 실행**: 같은 배치 안에서도 동일 `tenant_id`(파티션) 작업은 체이닝하여 순서를 보장하고, 다른 `tenant_id`끼리만 병렬 실행
- **완료 파티션 단위 즉시 오프셋 커밋 및 하트비트 유지**: 느린 작업 하나가 이미 끝난 다른 파티션의 오프셋 커밋을 막지 않도록 파티션별 오프셋 추적·커밋
- **실행 임대(lease)로 이어받기**: 작업을 실행하는 파드가 `task_budget`에 소유자와 갱신 시각을 남긴다. 재수신된 메시지나 주기 점검이 임대가 끊긴 미완료 작업만 체크포인트에서 이어 간다(살아 있는 파드의 작업을 다른 파드가 동시에 돌리지 않는다)
- **전역 동시성 세마포어(`_slots`) 통합**: Kafka 소비와 HTTP `/resume`·`recover`가 같은 워커 슬롯 풀을 공유하고, 가용 슬롯만큼만 `getmany()`로 가져오는 Backpressure 적용
- **파티션 수 환경변수 개방**: 기본값은 `3`으로 유지하고 `${KAFKA_PARTITIONS:3}`으로 열어두어 대규모 환경에서 코드 수정 없이 확장 가능하게 구성

### 하지 않는 것

- 외부 메시지 브로커(RabbitMQ, SQS)나 Debezium CDC, Redis 기반 분산 락 도입
- Kafka Streams나 별도 스케줄러 프로세스 분리 (`backend`와 `agent` 서비스 내에서 동작)
- 승인·PR 병합 재개 경로(`POST /agent/tasks/{id}/resume`)의 Kafka 전환 (기획안 원칙대로 재개는 계속 HTTP 사용)

## 4. 설계

### 4.1 전체 흐름도

```
[Spring Boot Backend]
  TaskService.create()
    └─ DB TX: ai_tasks (QUEUED) + task_outbox (PENDING) 원자적 저장
         │
         ▼
  TaskOutboxRelay (즉시 비동기 트리거 + @Scheduled 폴링)
    ├─ SELECT ... FOR UPDATE SKIP LOCKED (PENDING 레코드 조회)
    ├─ Kafka Producer (acks=all, enable.idempotence=true) 전송
    ├─ 성공 시: task_outbox.status = 'SENT'
    └─ 최대 재시도(5회) 초과 시: task_outbox.status = 'FAILED' + ai_tasks.status = 'FAILED'

[Kafka Broker (KRaft)]
  ├─ tasks.gold         (partitions: ${KAFKA_PARTITIONS:3}, key: tenant_id)
  ├─ tasks.silver       (partitions: ${KAFKA_PARTITIONS:3}, key: tenant_id)
  ├─ tasks.retry.gold   (partitions: ${KAFKA_PARTITIONS:3}, key: tenant_id, header: retry_after_ms)
  ├─ tasks.retry.silver (partitions: ${KAFKA_PARTITIONS:3}, key: tenant_id, header: retry_after_ms)
  └─ tasks.dlt          (partitions: 1, key: tenant_id)

[Python Agent (FastAPI + aiokafka)]
  전역 슬롯 Semaphore (_slots = settings.workers)
    ├─ HTTP /resume, recover() ──▶ 슬롯 획득 후 실행
    └─ Kafka Consumers (역할별 분리 가능: AGENT_CONSUMER_ROLE=all|gold|silver, 기본 all)
         ├─ Gold Consumer Loop   (tasks.gold, tasks.retry.gold)
         └─ Silver Consumer Loop (tasks.silver, tasks.retry.silver)
               │  * 단일(all) 모드에서는 Gold 대기 시 Silver 슬롯 획득 양보
               ▼
         파티션(tenant_id)별 그룹핑 체인 실행
           ├─ tenant-a: [task-1 ──▶ task-2] (순차)  ┐
           └─ tenant-b: [task-3]            (순차)  ┴─▶ 테넌트 간 병렬, 파티션 완료 즉시 개별 오프셋 커밋
```

### 4.2 데이터 (Flyway `V12__task_outbox.sql`)

```sql
-- 작업 전달 대기열 (Transactional Outbox).
-- ai_tasks 생성과 같은 트랜잭션에 기록해 DB 커밋과 메시지 발행 사이의 유실(Dual-Write)을 막는다.

CREATE TABLE task_outbox (
    id              BIGSERIAL   PRIMARY KEY,
    task_id         UUID        NOT NULL UNIQUE REFERENCES ai_tasks (id),
    payload         JSONB       NOT NULL,              -- TaskMessage 직렬화 본문 (등급·고객사 포함, 토픽과 키는 발행기가 정한다)
    trace_id        TEXT        NOT NULL,
    status          TEXT        NOT NULL DEFAULT 'PENDING', -- PENDING | SENT | FAILED
    attempts        INT         NOT NULL DEFAULT 0,
    last_error      TEXT,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at         TIMESTAMPTZ
);

CREATE INDEX task_outbox_pending ON task_outbox (next_attempt_at, id) WHERE status = 'PENDING';
```

### 4.3 영역별 보완 설계

#### (1) Transactional Outbox 및 Producer 정합성 (결정 1)

- **원자적 저장**: `TaskService.create()`는 `TransactionTemplate` 안에서 `tasks.create(...)`와 `outbox.enqueue(message)`를 함께 커밋한다.
- **즉시 릴레이 + 백그라운드 재시도 (`TaskOutboxRelay`)**:
  - 트랜잭션 커밋 직후 즉시 해당 레코드 발행을 시도해 평상시 지연을 0에 가깝게 유지한다.
  - 일시적 네트워크·브로커 오류로 실패하면 `attempts`를 올리고 `next_attempt_at`을 지수 백오프(`2^attempts`초)로 갱신한다.
  - 백그라운드 스케줄러(`@Scheduled(fixedDelay = 1000)`)가 미전송 건을 **임대해서** 재전송한다: `UPDATE task_outbox SET attempts = attempts + 1, next_attempt_at = now() + interval '30 seconds' WHERE id IN (SELECT id ... WHERE status = 'PENDING' AND next_attempt_at <= now() ORDER BY id LIMIT 20 FOR UPDATE SKIP LOCKED) RETURNING *`. 잠금은 이 문장 동안만 잡고, 전송은 트랜잭션 밖에서 한다(브로커 응답을 기다리는 동안 행 잠금을 쥐지 않는다). 여러 backend 인스턴스가 떠도 같은 건을 동시에 집지 않는다.
  - 발행은 지금의 `TaskDispatcher`(HTTP 또는 Kafka)를 그대로 부른다. HTTP 모드도 같은 대기열을 지나므로 agent가 잠깐 내려가 있어도 작업이 바로 FAILED가 되지 않는다.
  - 5회 시도 후에도 실패하면 `task_outbox.status = 'FAILED'`로 바꾸고 `tasks.complete(taskId, FAILED, "작업을 agent에 전달하지 못했다", ...)`를 호출해 좀비 작업을 막는다.
- **Producer 설정**: `application.yml`에 `acks: all`, `enable.idempotence: true`, `delivery.timeout.ms: 5000`, `request.timeout.ms: 3000`, `max.block.ms: 3000`을 명시한다.

#### (2) SLA 등급별·재시도 토픽 및 컨슈머 분리 (결정 2)

- 재시도 시에도 SLA 등급이 섞이지 않도록 재시도 토픽을 `tasks.retry.gold`, `tasks.retry.silver`로 나눈다 (기존 `tasks.retry` 대체).
- 컨슈머 그룹과 루프를 분리한다:
  - `opslens-agent-gold`: `tasks.gold`, `tasks.retry.gold` 구독
  - `opslens-agent-silver`: `tasks.silver`, `tasks.retry.silver` 구독
- 환경변수 `AGENT_CONSUMER_ROLE`(`all` | `gold` | `silver`, 기본 `all`):
  - **로컬·기본 구성(`all`)**: 한 컨테이너 안에서 Gold 루프와 Silver 루프를 모두 띄우되, Gold 루프가 슬롯을 기다리는 동안(`gold_waiting > 0`)에는 Silver 루프가 새 슬롯을 가져가지 않고 양보한다.
  - **스케일아웃 구성(`gold` / `silver`)**: 배포 시 환경변수만 나누어 Gold 전용 파드 풀과 Silver 전용 파드 풀을 물리적으로 독립 확장한다.

#### (3) 파티션·키(`tenant_id`) 내 순서 보장과 병렬 실행

- 배치에서 꺼낸 `records`를 한꺼번에 `asyncio.gather()`로 돌리지 않고, **`TopicPartition` 단위로 묶어서 순차 체인 코루틴**으로 실행한다. 오프셋 커밋이 파티션 단위라서, 키(`tenant_id`) 단위로 더 잘게 나누면 앞 오프셋이 끝나기 전에 뒤 오프셋을 커밋할 수 없다:
  - 같은 파티션(같은 고객사)에 속한 레코드들은 오프셋 순서대로 하나씩 실행된다.
  - 서로 다른 파티션(다른 고객사) 체인들끼리만 병렬로 실행된다.
- **파티션 단위 즉시 오프셋 커밋**:
  - 특정 파티션의 체인 실행이 끝나면 다른 느린 파티션을 기다리지 않고 즉시 `await consumer.commit({tp: OffsetAndMetadata(last.offset + 1, "")})`을 호출한다.

#### (4) 논블로킹 지연 재시도 (`retry_after_ms` 헤더 기반)

- 실패 시 재발행할 때 헤더에 `retry_after_ms = now_ms + (2 ** attempts) * 1000`을 기록하고, 원본 등급에 맞춰 `tasks.retry.gold` 또는 `tasks.retry.silver`로 보낸다.
- 컨슈머가 재시도 토픽에서 레코드를 꺼냈을 때 `now_ms < retry_after_ms`이면:
  - 컨슈머 루프 안에서 `asyncio.sleep()`으로 블로킹하지 않는다.
  - 해당 `TopicPartition`을 `consumer.seek(tp, record.offset)`으로 되돌리고 `consumer.pause(tp)`한 뒤, `retry_after_ms` 시각에 `consumer.resume(tp)`이 실행되도록 예약(`loop.call_later`)하고 즉시 다른 파티션을 처리한다.

#### (5) 장시간 LLM 실행 시 Rebalance 방어

- `AIOKafkaConsumer` 생성 시 장시간 실행 특성에 맞는 폴링·세션 파라미터를 명시한다:
  - `max_poll_interval_ms=300_000` (5분 — 작업 최대 한도 180초보다 여유 확보)
  - `session_timeout_ms=45_000`, `heartbeat_interval_ms=15_000`
- 폴러(Poller)와 파티션 워커 태스크를 분리한다:
  - 실행 중인 `TopicPartition`은 `consumer.pause(tp)`해 두고 백그라운드 태스크로 돌리며, 컨슈머 루프는 멈추지 않고 주기적으로 `getmany()`를 호출해 하트비트·리밸런스 폴링을 유지한다. 파티션 태스크가 끝나면 해당 파티션 오프셋을 커밋하고 `consumer.resume(tp)`한다.

#### (6) 실행 임대로 크래시·리밸런싱 재소비 처리 (`runner.py`)

- 현재 `runner.run()`은 `snapshot.values`가 있으면 `retry=True`일 때만 재개하고 나머지는 `"DUPLICATE"`로 버린다.
- "현재 프로세스에서 실행 중이 아니면 이어 간다"(개정 1)로는 부족하다. 리밸런싱은 파드가 죽을 때만이 아니라 파드를 늘릴 때도 일어나서, 다른 파드가 **아직 실행 중인** 작업을 넘겨받을 수 있다. 그 파드도 "내 프로세스에서는 실행 중이 아니다"라고 판단해 같은 Graph를 동시에 돌리면 승인 요청과 조치가 두 번 나간다.
- 그래서 실행 여부를 DB에 둔다. `task_budget`에 `owner`(파드 식별자)와 `heartbeat_at`을 더하고:
  - `_drive`는 시작할 때 `UPDATE task_budget SET running = true, owner = 나, heartbeat_at = now() WHERE task_id = … AND (NOT running OR owner = 나 OR heartbeat_at < now() - 임대 시간)`으로 임대를 잡는다. 못 잡으면 다른 파드가 실행 중이므로 건너뛴다(`"RUNNING_ELSEWHERE"`).
  - 실행하는 동안 15초마다 `heartbeat_at`을 갱신한다. 임대 시간은 60초다.
  - 재수신된 메시지(`retry=False`)는 체크포인트가 있고 `snapshot.next`가 남아 있으며 interrupt가 없을 때 임대를 잡아 이어 간다. 잡지 못하면 건너뛰고 커밋한다(실행 중인 파드가 끝낸다).
  - 각 파드는 60초마다 `running`인데 임대가 끊긴 작업을 찾아 이어 간다(지금 시작 때 한 번만 하는 `recover()`를 주기 점검으로 넓힌다). 죽은 파드가 다시 뜨지 않아도 작업이 남지 않는다.

#### (7) 전역 동시성 슬롯(`_slots`) 통합 및 파티션 설정 개방 (결정 3)

- `main.py`의 `_slots = asyncio.Semaphore(settings.workers)`를 HTTP `/run`, `/resume`, `recover`, `kafka_worker`가 **모두 공유**한다.
- `kafka_worker`는 빈 슬롯이 있을 때만 레코드를 꺼내 실행하여 `/resume` 요청과 합쳐도 전체 동시 실행 수가 `AGENT_WORKERS`를 넘지 않게 제어한다(Backpressure).
- 토픽 파티션 수는 `opslens.kafka.partitions: ${KAFKA_PARTITIONS:3}`으로 두어 기본값은 `3`을 유지하고 환경변수로만 조정할 수 있게 한다.

#### (8) 멀티 인스턴스 기동 대비 Python DB 마이그레이션 분리 (`Alembic` + `agent-migrate`, 결정 4)

- **배경 (문제점)**: 현재 `main.py`(`lifespan`)와 `runner.py`(`runner.setup()`)는 웹 서버 기동 시점마다 `await checkpointer.setup()`과 `CREATE TABLE IF NOT EXISTS task_budget` / `ALTER TABLE task_budget ...` DDL을 직접 실행한다. 동시에 실행하면 `CREATE TABLE IF NOT EXISTS`끼리도 카탈로그 중복 키 오류가 날 수 있다. 컨슈머를 여러 파드(`gold`/`silver` 스케일아웃)로 동시에 띄우면 PostgreSQL 카탈로그 락(`AccessExclusiveLock`) 경합이 발생하고, 런타임 코드에 DDL이 혼재되어 스키마 버전 이력(`alembic_version`)이 남지 않는다.
- **해결 구조 (방안 A — Python 표준 마이그레이션 분리)**:
  - `agent` 스키마(`task_budget`)의 테이블 형상은 Python 표준 마이그레이션 도구인 **Alembic**(`alembic/versions/*.py`)으로 관리한다. `docs` 스키마(`chunks`)는 `opslens_indexer` 소유이고 indexer가 1회성 작업으로 만들므로 범위에서 뺀다(agent 계정은 그 스키마에 DDL 권한이 없다).
  - 첫 리비전(`0001`)은 이미 테이블이 있는 DB에서도 돌 수 있게 `IF NOT EXISTS`로 쓴다. 둘째 리비전(`0002`)이 6절의 `owner`·`heartbeat_at`을 더한다.
  - 배포·기동 시 **1회성 마이그레이션 컨테이너(`agent-migrate`)**가 먼저 실행되어 ① `alembic upgrade head` (`agent.task_budget` 등 생성·변경) 및 ② `await checkpointer.setup()` (`agent.checkpoints*` 마이그레이션)을 단 한 번 수행하고 종료(`service_completed_successfully`)한다.
  - `runner.py`의 `runner.setup()`과 `main.py` `lifespan`의 `checkpointer.setup()` 호출을 제거하여, **`agent` 워커 컨테이너들은 런타임에 DDL 없이 순수 DML(`SELECT/INSERT/UPDATE`)만 수행**하도록 분리한다.

## 5. 코드 변경

| 위치 | 변경 내용 |
|---|---|
| `server/spring/backend/src/main/resources/db/migration/V12__task_outbox.sql` | `task_outbox` 테이블과 `PENDING` 부분 인덱스 생성 |
| `server/spring/backend/src/main/resources/application.yml` | `spring.kafka.producer` 신뢰성 설정(`acks: all`, `enable.idempotence: true` 등) 및 `opslens.kafka.partitions: ${KAFKA_PARTITIONS:3}` 추가 |
| `server/spring/backend/src/main/java/com/opslens/backend/task/TaskOutboxRepository.java` (새) | Outbox 저장(`enqueue`), 미전송 건 임대(`claim`), 상태 갱신(`markSent`, `retryLater`, `markFailed`) |
| `server/spring/backend/src/main/java/com/opslens/backend/task/TaskOutboxRelay.java` (새) | 트랜잭션 후 즉시 발행 시도 + `@Scheduled` 주기적 재시도 + 5회 실패 시 작업 `FAILED` 보상 처리 |
| `server/spring/backend/src/main/java/com/opslens/backend/task/TaskService.java` | `create()`에서 `tasks.create()`와 `outbox.enqueue()`를 같은 트랜잭션으로 묶고 `relay.kick()` 호출 |
| `server/spring/backend/src/main/java/com/opslens/backend/task/KafkaTaskDispatcher.java` | `kafka.send(record).get(5, TimeUnit.SECONDS)`로 브로커 ACK 확인. `tasks.retry.gold`, `tasks.retry.silver` 토픽 빈 등록 및 `${opslens.kafka.partitions:3}` 적용 |
| `server/python/agent/alembic/` & `src/agent/migrate.py` (새) | `agent` 스키마용 Alembic 마이그레이션(`0001_task_budget.py`, `0002_task_lease.py`) + `checkpointer.setup()` 1회 실행 엔트리포인트(`python -m agent.migrate`). 의존성 `alembic` 추가 |
| `docker-compose.yml` | 1회성 `agent-migrate` 서비스 추가 (`agent`는 `depends_on: agent-migrate (service_completed_successfully)`) |
| `server/python/agent/src/agent/config.py` | `consumer_role: str = _env("AGENT_CONSUMER_ROLE", "all")` (`all` \| `gold` \| `silver`) 추가 |
| `server/python/agent/src/agent/main.py` | 런타임 `checkpointer.setup()` 및 `runner.setup()` 호출 제거, 전역 `_slots`를 `kafka_worker.consume(runner, _slots)`에 전달 |
| `server/python/agent/src/agent/kafka_worker.py` | ① Gold/Silver 컨슈머 루프 분리 및 Gold 슬롯 우선권 부여 ② 파티션/키 단위 순차 실행 + 파티션별 즉시 오프셋 커밋 ③ `retry_after_ms` 헤더 기반 논블로킹 지연 재시도 ④ 실행 중 파티션 `pause`/`resume`으로 Poller 비블로킹 유지 |
| `server/python/agent/src/agent/runner.py` | 런타임 DDL 메서드(`setup()`) 제거. 실행 임대(`owner`, `heartbeat_at`) 잡기·갱신, 재수신된 미완료 작업 이어받기, 임대가 끊긴 작업 주기 점검 |
| `server/python/agent/tests/test_task_outcomes.py` | 키별 순서 보장, 논블로킹 재시도, 리밸런싱 후 체크포인트 재개, 슬롯 공유 단위 테스트 추가 |

## 6. 명령

```bash
backend 빌드:   cd server/spring/backend && ./gradlew build
backend 테스트: cd server/spring/backend && ./gradlew test
agent 테스트:   cd server/python/agent && uv run --frozen python -m unittest discover -s tests -v
전체 기동:      docker compose up -d --build   # Kafka가 기본 (2026-10-20부터)
```

## 7. 프로젝트 구조 (바뀌는 곳)

```
server/spring/backend/src/main/resources/db/migration/V12__task_outbox.sql
server/spring/backend/src/main/resources/application.yml
server/spring/backend/src/main/java/com/opslens/backend/task/
  TaskOutboxRepository.java, TaskOutboxRelay.java (새 파일)
  TaskService.java, KafkaTaskDispatcher.java
server/spring/backend/src/test/java/com/opslens/backend/task/TaskServiceTest.java
server/python/agent/src/agent/{config.py, main.py, kafka_worker.py, runner.py}
server/python/agent/tests/test_task_outcomes.py
docs/OpsLens-기획안.md
docs/README.md
```

## 8. 구현 순서

단계마다 단위·통합 검증 후 다음 단계로 넘어간다.

| 단계 | 내용 | 확인 방법 |
|---|---|---|
| 1 | `V12__task_outbox.sql`, `TaskOutboxRepository`, `TaskOutboxRelay`, `TaskService` 트랜잭션 연동, `KafkaTaskDispatcher` 동기 확인 및 토픽(`tasks.retry.gold/silver`, `${KAFKA_PARTITIONS:3}`) 설정 | `./gradlew test` 통과 (브로커 장애 모킹 시 Outbox 재시도 후 5회 초과 시 `FAILED` 전환 확인) |
| 2 | Alembic·`agent-migrate`, `runner.py` 실행 임대와 이어받기 | 단위 테스트: 임대가 살아 있으면 건너뛰고, 끊겼으면 체크포인트에서 이어 간다. 스택: `agent-migrate`가 끝난 뒤 agent가 뜬다 |
| 3 | `kafka_worker.py` 및 `main.py`, `config.py` 개편: 전역 `_slots` 공유, Gold/Silver 루프 분리(`AGENT_CONSUMER_ROLE`), 파티션 단위 체인 실행·즉시 커밋, `retry_after_ms` 논블로킹 지연 재시도 | `tests/test_task_outcomes.py` 단위 테스트 전체 통과 |
| 4 | `DISPATCH_MODE=kafka` 스택 기동 및 E2E 회귀 검증 | Kafka 프로필로 컨테이너 기동 후 작업 생성·승인·완료 흐름 및 E2E 통과 확인. HTTP 모드(기본) E2E도 회귀 확인 |

## 9. 테스트 전략

| 수준 | 대상 | 방법 |
|---|---|---|
| 단위 (backend) | ① 작업 생성 시 `ai_tasks`와 `task_outbox`가 함께 기록되는지 ② 전송 실패 시 재시도 후 최대 횟수 초과 시 작업이 `FAILED`로 마킹되는지 | JUnit 5 + Mockito (`TaskServiceTest`) |
| 단위 (agent) | ① 같은 `tenant_id` 레코드들이 순서대로 실행되는지 ② 다른 `tenant_id`는 병렬 실행되고 먼저 끝난 파티션이 즉시 커밋되는지 ③ 첫 Node 이후 중단된 작업이 `retry=False`로 재수신될 때 이어서 실행되는지 ④ `retry_after_ms` 미도달 시 다른 파티션을 막지 않는지 | `unittest.IsolatedAsyncioTestCase` (`tests/test_task_outcomes.py`) |
| 통합 (스택) | `DISPATCH_MODE=kafka` 기동 후 Gold·Silver 동시 인입 시 Gold 우선 처리 및 E2E 시나리오 통과 | `scripts/e2e.py` |

## 10. 경계

- **항상:** 새 마이그레이션(`V12`)으로만 스키마 변경, 기존 HTTP 모드(`DISPATCH_MODE=http`) 하위 호환 유지, 단위 테스트 통과 후 반영, 한국어 주석으로 설계 이유 기록
- **먼저 묻기:** 새로운 외부 의존성 추가, 기존 API 응답 형식 변경
- **하지 않기:** V1~V11 마이그레이션 수정, 승인 재개(`/resume`) 경로를 Kafka로 변경, 컨슈머 루프 안에서 블로킹 `sleep` 호출, `agent`가 backend의 `app` 스키마를 직접 수정

## 11. 성공 기준

1. 작업 생성 시 `ai_tasks`와 `task_outbox`가 한 트랜잭션으로 저장되고, 브로커 일시 중단 후 복구되면 Outbox Relay가 자동으로 재전송해 작업이 정상 진행된다. 브로커가 계속 죽어 있으면 5회 재시도 후 `FAILED`로 전환되어 `QUEUED` 좀비 작업이 남지 않는다.
2. 같은 고객사(`tenant-a`) 작업 3개가 동시에 발행되면 1번 작업이 끝난(또는 `interrupt`에 도달한) 뒤에 2번 작업이 실행된다. 서로 다른 고객사(`tenant-a`, `tenant-b`) 작업은 동시에 실행된다.
3. `tasks.retry.*`에 백오프 대기 중인 작업이 있어도 새로 들어온 `tasks.gold` 작업이 대기 없이 즉시 소비된다.
4. 배치 내 작업 A(5초 소요)와 작업 B(90초 소요, 다른 파티션)가 함께 소비되었을 때, 작업 A의 파티션 오프셋은 5초 시점에 즉시 커밋된다.
5. 작업 실행 중(Node 1 완료 후 Node 2 진입 전) 실행하던 파드가 죽고 다른 파드가 원본 토픽에서 같은 메시지를 다시 읽으면, 임대가 끊긴 뒤 `"DUPLICATE"`로 버리지 않고 Node 2부터 이어서 완료한다. 실행하던 파드가 살아 있으면 다시 읽은 파드는 실행하지 않는다.
6. Kafka 소비 작업과 HTTP `/resume` 작업의 동시 실행 합이 `AGENT_WORKERS` 한도를 넘지 않는다.

## 12. 결정 사항 (2026-10-20)

| # | 질문 | 결정 | 반영 위치 |
|---|---|---|---|
| 1 | Producer 정합성 수준 | **Transactional Outbox 테이블(`V12__task_outbox.sql`)까지 도입**한다 | 2장 가정 2, 4.1, 4.2, 4.3(1), 5장, 8장 1단계 |
| 2 | 컨슈머 분리 기본값 | 기본 구성은 **`AGENT_CONSUMER_ROLE=all`**로 한 컨테이너가 두 루프를 돌리고, 스케일아웃 시 `gold`/`silver`로 분리한다 | 2장 가정 4, 4.1, 4.3(2), 5장 |
| 3 | 기본 파티션 수 | 기본값은 **`3`으로 유지**하고 환경변수(`KAFKA_PARTITIONS`)로만 열어둔다 | 2장 가정 5, 4.1, 4.3(7), 5장 |
| 4 | Python(`agent`) DB 마이그레이션 방식 | `runner.py` / `main.py`의 런타임 DDL(`setup()`)을 제거하고, **Python 표준 `Alembic` + 1회성 `agent-migrate` 컨테이너**로 분리하여 멀티 파드 기동 시 DDL 락 경합을 방지한다 | 4.3(8), 5장 |

## 13. 코드 대조 검토 (2026-10-20, 개정 2)

1장의 결함 7개는 코드와 맞다(`kafka.send()` 결과 미확인, 배치 `gather`와 일괄 커밋, 루프 안 `sleep`, `_slots` 미공유, 파티션 3 고정). 아래는 개정 1을 그대로 구현하면 틀리거나 위험한 부분과 바꾼 내용이다.

| # | 개정 1 | 문제 | 개정 2 |
|---|---|---|---|
| 1 | 마이그레이션 `V10__task_outbox.sql` | `V10__autoscaler_action.sql`, `V11__gitops_branch.sql`이 이미 있다. Flyway가 같은 버전으로 기동에 실패한다 | `V12__task_outbox.sql` |
| 2 | 재수신 시 "현재 프로세스에서 실행 중이 아니면" 이어 감 (`_active_tasks`) | 파드를 늘릴 때의 리밸런싱은 실행 중인 파드가 살아 있는데도 파티션을 넘긴다. 넘겨받은 파드가 같은 작업을 동시에 돌려 승인 요청·조치가 두 번 나갈 수 있다 | DB 실행 임대(`owner`, `heartbeat_at`, 60초)와 주기 점검 (4.3(6)) |
| 3 | 결함 6 "작업이 유실된다" | 파드 1개 재시작은 `task_budget.running` + 시작 때 `recover()`로 이미 이어 간다. 유실은 파드가 여러 개이고 죽은 파드가 다시 뜨지 않을 때다 | 1장 표 6행 수정 |
| 4 | Alembic이 `docs` 스키마(`chunks`)도 관리 | `docs`는 `opslens_indexer` 소유이고 indexer가 만든다. agent 계정은 DDL 권한이 없다 | `agent` 스키마만 |
| 5 | Outbox Relay가 `FOR UPDATE SKIP LOCKED`로 집고 전송 | 브로커 응답(최대 5초)을 기다리는 동안 행 잠금과 DB 연결을 쥔다 | 짧은 `UPDATE … RETURNING`으로 임대(30초)한 뒤 트랜잭션 밖에서 전송 |
| 6 | Outbox에 `topic`(`tasks.gold`·`http`)·`partition_key` 저장 | 토픽과 키는 발행기(`KafkaTaskDispatcher`)가 등급과 고객사로 정한다. 두 곳에서 정하면 어긋날 수 있다 | `payload`만 저장하고 Relay가 `TaskDispatcher`를 부른다(HTTP 모드도 같은 대기열) |
| 7 | 파티션 또는 키 단위 순차 실행 | 커밋이 파티션 단위라 키 단위로 나누면 커밋 순서를 지킬 수 없다 | 파티션 단위. 같은 파티션에 해시된 다른 고객사도 순서대로 돈다(파티션 수로 조절) |

**알고 넘어갈 비용**

- 같은 파티션 안에서는 순서대로 돌기 때문에, 한 고객사의 인시던트 작업(회복 확인까지 최대 약 3분)이 끝나거나 승인 대기에 닿을 때까지 같은 파티션의 질문 작업이 기다린다. 승인 대기(interrupt)에 닿으면 바로 다음으로 넘어가므로 대부분은 짧다.
- 2026-10-20부터 Kafka가 기본 작업 전달 방식이다(compose 기본값, `queue` 프로필 제거). HTTP 모드는 `DISPATCH_MODE=http`로 남는다.

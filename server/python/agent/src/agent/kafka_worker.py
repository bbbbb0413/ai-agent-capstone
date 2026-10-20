"""Kafka 작업 소비자 (DISPATCH_MODE=kafka, SPEC-kafka-scale 4.3).

- 등급별로 소비 루프와 컨슈머 그룹을 나눈다: Gold(tasks.gold, tasks.retry.gold), Silver(tasks.silver, tasks.retry.silver).
  AGENT_CONSUMER_ROLE=all이면 한 프로세스가 둘 다 돌리고, gold·silver면 그 등급만 돌린다(파드를 등급별로 늘린다).
- 실행 슬롯은 HTTP 재개(/resume)·복구와 같은 세마포어를 쓴다. 동시에 도는 작업이 AGENT_WORKERS를 넘지 않는다.
  Gold가 슬롯을 기다리는 동안 Silver는 새 슬롯을 가져가지 않는다.
- 파티션 하나의 레코드는 오프셋 순서대로 하나씩 실행하고(같은 고객사는 같은 파티션), 다른 파티션끼리는 동시에 실행한다.
  실행 중인 파티션은 pause해 두고 루프는 계속 poll한다. 오래 걸리는 작업이 하트비트·리밸런스 응답을 막지 않는다.
- 레코드 하나가 끝나면(완료, 실패 후 재시도 발행, 또는 승인 대기 interrupt) 그 파티션 오프셋을 바로 커밋한다.
  재개는 Kafka가 아니라 HTTP(/agent/tasks/{id}/resume)로 한다.
- 실패한 작업은 등급별 재시도 토픽에 retry_after_ms 헤더와 함께 보낸다. 재시도 레코드는 그 시각까지 자기 파티션만 기다린다.
  MAX_ATTEMPTS번 실패하면 tasks.dlt로 보내고 FAILED로 기록한다.
- 같은 작업이 다시 와도(중복 소비, 리밸런싱) runner의 실행 임대가 동시에 두 번 돌지 않게 막는다.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, ConsumerRecord, TopicPartition
from aiokafka.errors import CommitFailedError, IllegalStateError, KafkaConnectionError

from agent.backend import backend
from agent.config import settings
from agent.runner import Runner

log = logging.getLogger(__name__)

DLT = "tasks.dlt"
TOPICS = {"gold": ("tasks.gold", "tasks.retry.gold"), "silver": ("tasks.silver", "tasks.retry.silver")}
MAX_ATTEMPTS = 3
POLL_TIMEOUT_MS = 500
# 작업 하나가 회복 확인까지 약 3분 걸린다. 그보다 길게 두어야 실행 중 poll 간격 때문에 그룹에서 빠지지 않는다
# (루프는 계속 poll하므로 보통은 닿지 않는다)
CONSUMER_OPTIONS = {"max_poll_interval_ms": 300_000, "session_timeout_ms": 45_000, "heartbeat_interval_ms": 15_000}


@dataclass
class Slots:
    """전역 실행 슬롯과 Gold 우선권. 세마포어는 main의 HTTP 재개·복구와 같은 것이다."""
    semaphore: asyncio.Semaphore
    gold_waiting: int = 0
    _gold_done: asyncio.Event = field(default_factory=asyncio.Event)

    async def acquire(self, tier: str) -> None:
        if tier == "gold":
            self.gold_waiting += 1
            try:
                await self.semaphore.acquire()
            finally:
                self.gold_waiting -= 1
                if not self.gold_waiting:
                    self._gold_done.set()
            return
        while True:   # Silver: Gold가 기다리는 동안 양보한다
            while self.gold_waiting:
                self._gold_done.clear()
                await self._gold_done.wait()
            await self.semaphore.acquire()
            if not self.gold_waiting:
                return
            self.semaphore.release()   # 슬롯을 잡는 사이 Gold가 줄을 섰다. 돌려주고 다시 기다린다

    def release(self) -> None:
        self.semaphore.release()


def retry_after_ms(record: ConsumerRecord) -> int:
    headers = {k: v.decode() for k, v in record.headers or []}
    return int(headers.get("retry_after_ms", "0"))


class TierWorker:
    """한 등급의 소비 루프. 컨슈머·프로듀서·runner를 받아서 테스트에서 가짜로 바꿀 수 있다."""

    def __init__(self, tier: str, consumer, producer, runner: Runner, slots: Slots) -> None:
        self.tier = tier
        self.main_topic, self.retry_topic = TOPICS[tier]
        self.consumer, self.producer, self.runner, self.slots = consumer, producer, runner, slots
        self.chains: dict[TopicPartition, asyncio.Task] = {}

    async def run(self) -> None:
        while True:
            batch = await self.consumer.getmany(timeout_ms=POLL_TIMEOUT_MS)
            self.dispatch(batch)

    def dispatch(self, batch: dict[TopicPartition, list[ConsumerRecord]]) -> None:
        """받은 묶음을 파티션별 순차 실행으로 넘긴다. 실행 중인 파티션은 끝날 때까지 더 받지 않는다."""
        for tp, records in batch.items():
            if not records:
                continue
            if tp in self.chains:
                # 리밸런싱으로 다시 배정되면 pause가 풀려 실행 중인 레코드를 또 받을 수 있다. 되돌려 두고 끝난 뒤 다시 받는다
                self.consumer.seek(tp, records[0].offset)
                self.consumer.pause(tp)
                continue
            self.consumer.pause(tp)
            task = asyncio.create_task(self._chain(tp, records))
            self.chains[tp] = task
            task.add_done_callback(lambda _, tp=tp: self._chain_done(tp))

    def _chain_done(self, tp: TopicPartition) -> None:
        self.chains.pop(tp, None)
        if tp in self.consumer.assignment():
            self.consumer.resume(tp)

    async def _chain(self, tp: TopicPartition, records: list[ConsumerRecord]) -> None:
        for record in records:
            if (wait := retry_after_ms(record) / 1000 - time.time()) > 0:
                await asyncio.sleep(wait)   # 이 파티션만 기다린다. 다른 파티션과 poll은 계속된다
            await self.slots.acquire(self.tier)
            try:
                await self._handle(record)
            finally:
                self.slots.release()
            await self._commit(tp, record.offset + 1)

    async def _commit(self, tp: TopicPartition, offset: int) -> None:
        try:
            await self.consumer.commit({tp: offset})
        except (CommitFailedError, IllegalStateError) as e:
            # 리밸런싱으로 파티션을 넘겼다. 새 담당이 다시 읽어도 runner 임대가 중복 실행을 막는다
            log.warning("commit %s@%d skipped: %s", tp, offset, e)

    async def _handle(self, record: ConsumerRecord) -> None:
        message = json.loads(record.value)
        headers = {k: v.decode() for k, v in record.headers or []}
        attempts = int(headers.get("attempts", "0"))
        try:
            await self.runner.run(message, raise_errors=True)
        except Exception as e:  # noqa: BLE001 — 작업 실패는 재시도 토픽으로 넘기고 소비는 계속한다
            attempts += 1
            topic = DLT if attempts >= MAX_ATTEMPTS else self.retry_topic
            out = [("task_id", str(message["taskId"]).encode()), ("trace_id", message["traceId"].encode()),
                   ("attempts", str(attempts).encode()), ("error", type(e).__name__.encode()),
                   ("retry_after_ms", str(int(time.time() * 1000) + 2 ** attempts * 1000).encode())]
            await self.producer.send_and_wait(topic, record.value, key=record.key, headers=out)
            log.warning("task %s failed (%s), sent to %s", message["taskId"], e, topic)
            if topic == DLT:
                await backend.result(str(message["taskId"]), status="FAILED",
                                     answer=f"{MAX_ATTEMPTS}번 시도했지만 실패해 DLT로 보냈다: {type(e).__name__}")


def tiers(role: str) -> list[str]:
    if role not in ("all", "gold", "silver"):
        raise ValueError(f"AGENT_CONSUMER_ROLE은 all, gold, silver 중 하나다: {role}")
    return ["gold", "silver"] if role == "all" else [role]


RECONNECT_SECONDS = 5


async def consume(runner: Runner, semaphore: asyncio.Semaphore) -> None:
    """브로커에 닿지 않으면(아직 뜨는 중, 일시 장애) 기다렸다가 다시 연결한다. 한 번 실패로 소비가 멈추지 않게 한다."""
    slots = Slots(semaphore)
    while True:
        try:
            await _consume(runner, slots)
        except KafkaConnectionError as e:
            log.warning("Kafka unavailable (%s), reconnecting in %ds", e, RECONNECT_SECONDS)
            await asyncio.sleep(RECONNECT_SECONDS)


async def _consume(runner: Runner, slots: Slots) -> None:
    producer = AIOKafkaProducer(bootstrap_servers=settings.kafka_bootstrap_servers, acks="all",
                                enable_idempotence=True)
    consumers = [AIOKafkaConsumer(*TOPICS[tier], bootstrap_servers=settings.kafka_bootstrap_servers,
                                  group_id=f"opslens-agent-{tier}", enable_auto_commit=False,
                                  auto_offset_reset="earliest", **CONSUMER_OPTIONS)
                 for tier in tiers(settings.consumer_role)]
    try:
        await producer.start()
        for consumer in consumers:
            await consumer.start()
        await asyncio.gather(*(TierWorker(tier, consumer, producer, runner, slots).run()
                               for tier, consumer in zip(tiers(settings.consumer_role), consumers)))
    finally:
        for consumer in consumers:
            await consumer.stop()
        await producer.stop()

"""Kafka 작업 소비자 (DISPATCH_MODE=kafka, 가산점).

- Kafka에는 우선순위가 없어서 SLA 등급별 토픽을 둔다. 한 번에 꺼낸 묶음에서 Gold를 먼저 처리하고,
  Gold가 밀려 있으면 Silver 파티션을 잠시 멈춘다(pause).
- Graph는 interrupt(승인 대기)나 종료에서 돌아오므로 승인 대기가 consumer를 막지 않는다.
  그 시점에 오프셋을 커밋한다. 재개는 Kafka가 아니라 HTTP(/agent/tasks/{id}/resume)로 한다.
- 실패한 작업은 tasks.retry로 다시 보내고, 3번 실패하면 tasks.dlt로 보낸 뒤 FAILED로 기록한다.
"""

import asyncio
import json
import logging

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, ConsumerRecord

from agent.backend import backend
from agent.config import settings
from agent.runner import Runner

log = logging.getLogger(__name__)

GOLD, SILVER, RETRY, DLT = "tasks.gold", "tasks.silver", "tasks.retry", "tasks.dlt"
PRIORITY = {GOLD: 0, RETRY: 1, SILVER: 2}
MAX_ATTEMPTS = 3


async def consume(runner: Runner) -> None:
    consumer = AIOKafkaConsumer(
        GOLD, SILVER, RETRY,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id="opslens-agent",
        enable_auto_commit=False,
        auto_offset_reset="earliest",
    )
    producer = AIOKafkaProducer(bootstrap_servers=settings.kafka_bootstrap_servers)
    await consumer.start()
    await producer.start()
    try:
        while True:
            batch = await consumer.getmany(timeout_ms=1000, max_records=settings.workers)
            records = sorted((r for rs in batch.values() for r in rs), key=lambda r: PRIORITY[r.topic])
            if not records:
                continue
            silver = [tp for tp in consumer.assignment() if tp.topic == SILVER]
            if any(r.topic == GOLD for r in records):
                consumer.pause(*silver)
            else:
                consumer.resume(*silver)
            await asyncio.gather(*(_handle(runner, producer, r) for r in records))
            await consumer.commit()
    finally:
        await consumer.stop()
        await producer.stop()


async def _handle(runner: Runner, producer: AIOKafkaProducer, record: ConsumerRecord) -> None:
    message = json.loads(record.value)
    headers = {k: v.decode() for k, v in record.headers}
    attempts = int(headers.get("attempts", "0"))
    if record.topic == RETRY:
        await asyncio.sleep(2 ** attempts)  # 재시도 간격을 점점 늘린다
    try:
        await runner.run(message, raise_errors=True, retry=record.topic == RETRY)
    except Exception as e:
        attempts += 1
        out = [("task_id", str(message["taskId"]).encode()), ("trace_id", message["traceId"].encode()),
               ("attempts", str(attempts).encode()), ("error", type(e).__name__.encode())]
        topic = DLT if attempts >= MAX_ATTEMPTS else RETRY
        await producer.send_and_wait(topic, record.value, key=record.key, headers=out)
        log.warning("task %s failed (%s), sent to %s", message["taskId"], e, topic)
        if topic == DLT:
            await backend.result(str(message["taskId"]), status="FAILED",
                                 answer=f"{MAX_ATTEMPTS}번 시도했지만 실패해 DLT로 보냈다: {type(e).__name__}")

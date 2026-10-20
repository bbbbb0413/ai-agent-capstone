"""Kafka 소비 순서·커밋·재시도·슬롯 (SPEC-kafka-scale 11장 성공 기준 2~4, 6). 브로커 없이 가짜 컨슈머로 확인한다."""

import asyncio
import json
import time
import unittest
from unittest.mock import AsyncMock

from aiokafka import TopicPartition
from aiokafka.structs import ConsumerRecord

from agent import kafka_worker
from agent.kafka_worker import Slots, TierWorker


def record(topic: str, partition: int, offset: int, task: str, headers=()) -> ConsumerRecord:
    value = json.dumps({"taskId": task, "tenantId": "t", "traceId": "x", "type": "QUESTION"}).encode()
    return ConsumerRecord(topic, partition, offset, 0, 0, b"t", value, None, 0, len(value),
                          [(k, v.encode()) for k, v in headers])


class FakeConsumer:
    def __init__(self, partitions):
        self.assigned = set(partitions)
        self.commits: list[tuple[TopicPartition, int, float]] = []
        self.paused, self.resumed = [], []

    def assignment(self):
        return self.assigned

    def pause(self, *tps):
        self.paused += tps

    def resume(self, *tps):
        self.resumed += tps

    def seek(self, tp, offset):
        pass

    async def commit(self, offsets):
        for tp, offset in offsets.items():
            self.commits.append((tp, offset, time.monotonic()))


class FakeRunner:
    def __init__(self, seconds: dict[str, float], fail: set[str] = frozenset()):
        self.seconds, self.fail = seconds, fail
        self.started, self.finished = [], []
        self.running = self.peak = 0

    async def run(self, message, raise_errors=False):
        task = message["taskId"]
        self.started.append(task)
        self.running += 1
        self.peak = max(self.peak, self.running)
        try:
            await asyncio.sleep(self.seconds.get(task, 0))
            if task in self.fail:
                raise RuntimeError("boom")
        finally:
            self.running -= 1
            self.finished.append(task)
        return "DONE"


class KafkaWorkerTests(unittest.IsolatedAsyncioTestCase):
    def worker(self, runner, partitions, slots=4, tier="gold"):
        consumer = FakeConsumer(partitions)
        producer = AsyncMock()
        return TierWorker(tier, consumer, producer, runner, Slots(asyncio.Semaphore(slots))), consumer, producer

    async def drain(self, worker):
        while worker.chains:
            await asyncio.gather(*worker.chains.values())

    async def test_same_partition_runs_in_order_and_other_partitions_run_together(self):
        p0, p1 = TopicPartition("tasks.gold", 0), TopicPartition("tasks.gold", 1)
        runner = FakeRunner({"a1": 0.05, "a2": 0.0, "b1": 0.0})
        worker, consumer, _ = self.worker(runner, [p0, p1])
        worker.dispatch({p0: [record("tasks.gold", 0, 0, "a1"), record("tasks.gold", 0, 1, "a2")],
                         p1: [record("tasks.gold", 1, 0, "b1")]})
        await self.drain(worker)
        self.assertLess(runner.finished.index("a1"), runner.started.index("a2"))   # 같은 파티션은 순서대로
        self.assertLess(runner.finished.index("b1"), runner.finished.index("a1"))  # 다른 파티션은 기다리지 않는다
        self.assertEqual(sorted(consumer.paused, key=str), sorted([p0, p1], key=str))
        self.assertEqual(sorted(consumer.resumed, key=str), sorted([p0, p1], key=str))

    async def test_fast_partition_is_committed_before_slow_one_finishes(self):
        slow, fast = TopicPartition("tasks.gold", 0), TopicPartition("tasks.gold", 1)
        runner = FakeRunner({"slow": 0.2, "fast": 0.0})
        worker, consumer, _ = self.worker(runner, [slow, fast])
        started = time.monotonic()
        worker.dispatch({slow: [record("tasks.gold", 0, 5, "slow")], fast: [record("tasks.gold", 1, 7, "fast")]})
        await self.drain(worker)
        commits = {tp: (offset, at - started) for tp, offset, at in consumer.commits}
        self.assertEqual(commits[fast][0], 8)
        self.assertEqual(commits[slow][0], 6)
        self.assertLess(commits[fast][1], 0.1)   # 느린 파티션(0.2초)을 기다리지 않고 커밋했다

    async def test_delayed_retry_does_not_block_other_partitions(self):
        retry, fresh = TopicPartition("tasks.retry.gold", 0), TopicPartition("tasks.gold", 0)
        runner = FakeRunner({})
        worker, _, _ = self.worker(runner, [retry, fresh])
        later = str(int(time.time() * 1000) + 300)
        worker.dispatch({retry: [record("tasks.retry.gold", 0, 0, "retry", [("retry_after_ms", later)])],
                         fresh: [record("tasks.gold", 0, 0, "fresh")]})
        await asyncio.sleep(0.05)
        self.assertEqual(runner.finished, ["fresh"])   # 재시도는 아직 기다리는 중
        await self.drain(worker)
        self.assertEqual(runner.finished, ["fresh", "retry"])

    async def test_failure_goes_to_tier_retry_topic_with_backoff_header(self):
        tp = TopicPartition("tasks.silver", 0)
        runner = FakeRunner({}, fail={"bad"})
        worker, consumer, producer = self.worker(runner, [tp], tier="silver")
        worker.dispatch({tp: [record("tasks.silver", 0, 3, "bad")]})
        await self.drain(worker)
        topic = producer.send_and_wait.await_args.args[0]
        headers = dict(producer.send_and_wait.await_args.kwargs["headers"])
        self.assertEqual(topic, "tasks.retry.silver")
        self.assertGreater(int(headers["retry_after_ms"]), int(time.time() * 1000))
        self.assertEqual(consumer.commits[0][1], 4)   # 재시도로 넘긴 레코드도 커밋해 다시 읽지 않는다

    async def test_last_attempt_goes_to_dlt_and_fails_task(self):
        tp = TopicPartition("tasks.retry.gold", 0)
        runner = FakeRunner({}, fail={"bad"})
        worker, _, producer = self.worker(runner, [tp])
        with unittest.mock.patch.object(kafka_worker.backend, "result", AsyncMock()) as result:
            worker.dispatch({tp: [record("tasks.retry.gold", 0, 0, "bad", [("attempts", "2")])]})
            await self.drain(worker)
        self.assertEqual(producer.send_and_wait.await_args.args[0], kafka_worker.DLT)
        self.assertEqual(result.await_args.kwargs["status"], "FAILED")

    async def test_shared_slots_cap_concurrency(self):
        tps = [TopicPartition("tasks.gold", i) for i in range(3)]
        runner = FakeRunner({f"t{i}": 0.03 for i in range(3)})
        worker, _, _ = self.worker(runner, tps, slots=1)
        worker.dispatch({tp: [record("tasks.gold", tp.partition, 0, f"t{tp.partition}")] for tp in tps})
        await self.drain(worker)
        self.assertEqual(runner.peak, 1)

    async def test_silver_yields_slot_while_gold_waits(self):
        slots = Slots(asyncio.Semaphore(1))
        order = []
        await slots.acquire("gold")   # 슬롯 하나를 쓰는 중

        async def take(tier):
            await slots.acquire(tier)
            order.append(tier)
            await asyncio.sleep(0)
            slots.release()

        silver = asyncio.create_task(take("silver"))
        await asyncio.sleep(0)
        gold = asyncio.create_task(take("gold"))
        await asyncio.sleep(0)
        slots.release()
        await asyncio.gather(silver, gold)
        self.assertEqual(order, ["gold", "silver"])

    def test_consumer_role(self):
        self.assertEqual(kafka_worker.tiers("all"), ["gold", "silver"])
        self.assertEqual(kafka_worker.tiers("silver"), ["silver"])
        with self.assertRaises(ValueError):
            kafka_worker.tiers("bronze")


if __name__ == "__main__":
    unittest.main()

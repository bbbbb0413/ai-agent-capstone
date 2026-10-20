package com.opslens.backend.task;

/**
 * 작업을 agent에 넘긴다. opslens.dispatch-mode로 구현을 고른다.
 *   kafka : 기본. SLA 등급별 토픽(tasks.gold, tasks.silver)에 발행한다
 *   http  : agent /agent/tasks/{id}/run을 바로 호출한다 (Kafka 없이 원인을 좁힐 때)
 */
public interface TaskDispatcher {

    void dispatch(TaskMessage message);
}

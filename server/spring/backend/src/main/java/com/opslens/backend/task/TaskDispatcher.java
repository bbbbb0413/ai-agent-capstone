package com.opslens.backend.task;

/**
 * 작업을 agent에 넘긴다. opslens.dispatch-mode로 구현을 고른다.
 *   http  : MVP. agent /agent/tasks/{id}/run을 바로 호출한다
 *   kafka : 가산점. SLA 등급별 토픽(tasks.gold, tasks.silver)에 발행한다
 */
public interface TaskDispatcher {

    void dispatch(TaskMessage message);
}

## 조치 제안 단계

아래 순서로 판단하고, 마지막에 도구 하나를 호출한다.

1. **장애 형태를 정한다.** 진단 결과의 근거(로그 메시지, 메트릭, 직전 변경)를 공통 런북 RB-STD 조항의 증상·구분과 맞춰 하나를 고른다.
   - 근거는 로그와 메트릭이다. 서비스 목록의 `dependencies`에 의존 대상이 있다는 것만으로 그 대상을 원인으로 보지 않는다. 그 대상의 오류 로그(예: `payment API timeout`, `slow query`)가 있을 때만 의존 대상 문제(RB-STD-04·05)다.
   - 함께 준 지표와 로그 신호(코드가 조회)로 아래 표에서 행을 고른다. 진단 결과와 표가 다르면 표를 따른다(진단 결과에 표와 다른 근거가 적혀 있을 때만 예외).

   | 에러율(`error_rate`) | 로그 신호 | 장애 형태 |
   |---|---|---|
   | 0.05 이상 | `connection_pool_exhausted`, 직전 설정 변경 있음 | RB-STD-01 커넥션 풀 고갈 |
   | 0.05 이상 | `worker_pool_saturated` | RB-STD-02 작업 스레드 고갈 |
   | 0.05 미만 | `request_queue_backlog`, 의존 대상 모두 HEALTHY | RB-STD-03 트래픽 급증 |
   | 상관없음 | `external_api_timeout` | RB-STD-04 외부 의존 지연 |
   | 상관없음 | `slow_query` 또는 DB 의존 대상 DEGRADED·DOWN | RB-STD-05 DB 자체 지연 |
   | 위에 맞는 행이 없음 | | RB-STD-07 원인 미확정 |

2. **후보 조치를 정한다.** 그 조항의 조치를 따른다. 1차·2차 조치가 나뉘어 있으면 1차 조치부터 제안한다. 서비스 런북의 주의(예: 단일 레플리카 재시작은 전체 중단)는 조치를 바꾸지 않고 이유에 적는다.
3. **할 수 있는지 확인한다.** 서비스 목록의 계산값을 본다: `supportedActions`(계약과 실행 환경이 모두 허용하는 조치), `safeMaxReplicas`(확장 상한), `stateful`, `autoscaling`(HPA).
   - 트래픽 급증(RB-STD-03)이고 `autoscaling`이 있으면 레플리카를 직접 바꾸지 않는다. `atMax`가 false면 HPA가 늘리는 중이므로 **도구를 부르지 않고** "HPA가 확장 중"이라는 이유만 적는다. `atMax`가 true이고 `canRaiseMax`가 true면 `adjust_autoscaler`로 최대치를 `safeMaxReplicas`까지 올린다. `canRaiseMax`가 false면 에스컬레이션한다: `category="ACTION_BLOCKED"`, `required_action="adjust_autoscaler"`, `blocked_by="STRUCTURE"`. 최대치가 이미 `safeMaxReplicas`(DB 연결 한도로 계산한 구조 상한)라 막힌 것이고, 계약 상한은 그보다 높으므로 `CONTRACT`가 아니다.
   - 도구를 부르지 않는 것은 위 경우뿐이다. 그 밖에는 반드시 도구 하나를 호출한다.
   - **2단계 조치를 할 수 없으면 다른 조치로 대신하지 않는다.** `supportedActions`에 있는 다른 조치(예: 재시작)가 그 장애 형태를 고치지 못하면 고르지 않고 에스컬레이션한다. 예: 트래픽 급증에 확장이 계약상 불가하면 재시작이 아니라 `ACTION_BLOCKED`, `required_action="scale_deployment"`, `blocked_by="CONTRACT"`다. 재시작은 처리 용량을 늘리지 않는다.
4. **호출한다.**
   - 할 수 있으면 조치를 호출한다. `service`에는 장애가 난 서비스 이름을 넣는다.
     - 파드 재시작: `business_execute_runbook_action(action="restart_pods", service=…)`
     - 레플리카 확장: `business_execute_runbook_action(action="scale_deployment", service=…, replicas=…)`
     - 직전 배포로 롤백: `business_execute_runbook_action(action="rollback_deployment", service=…)`
     - HPA 최대치 올리기: `business_execute_runbook_action(action="adjust_autoscaler", service=…, max_replicas=…)`
   - 조항이 앱 조치를 하지 않는다고 하면(RB-STD-04 서드파티 외부 의존) 고객 공지(`business_send_customer_notice`)를 제안한다.
   - 필요한 조치를 할 수 없거나 원인이 고객사 다른 팀 소유이면 에스컬레이션(`business_escalate_incident`)을 제안한다.
     - 조치가 `supportedActions`에 없거나, 확장이 `safeMaxReplicas`를 넘거나, 상태 저장 서비스면: `category="ACTION_BLOCKED"`, `required_action`에 필요한 조치, `blocked_by`에 `CONTRACT`(계약)·`STRUCTURE`(상한·상태 저장)·`RUNTIME`(지원하지 않는 실행 환경) (RB-STD-06)
     - 1단계에서 원인이 `owner`가 `tenant`인 의존 대상(예: DB)으로 정해졌으면: `category="DEPENDENCY"`, `dependency`에 그 이름 (RB-STD-05)
     - `reason`에는 필요한 조치와 할 수 없는 이유(계약 조항, 상한 값)를 한 문장으로 적는다

- 반영 방식(바로 실행 또는 설정 저장소 PR)은 backend가 고객사에 맞춰 정한다. 제안에서 반영 방식을 고르지 않는다.
- 고객 공지는 조치를 대신하지 않는다. 공지는 보고서를 쓴 뒤 따로 승인을 받아 보낸다. 할 수 있는 조치가 있으면 조치를 고른다. 에스컬레이션할 때도 공지를 함께 고르지 않는다.
- 호출과 함께 이유를 두 문장 이내로 적고, 근거가 된 런북 조항 번호를 넣는다. 조항 번호는 위에 주어진 런북·계약 조항에 있는 것만 쓰고, 조항이 비어 있으면 "근거 조항 없음"이라고 적는다. 같은 내용을 `reason` 인자에도 한 문장으로 넣는다.
- 위 3단계의 HPA 확장 중인 경우를 빼면 반드시 도구 하나를 호출한다.

# Harness 명세

PDF 표 6-3의 권장값을 기준으로 둔 값과, 팀이 바꾼 값과 이유를 기록한다. 코드는 `server/python/agent/src/agent/harness.py`에 있다.

## 현재 값

| 구성 요소 | 항목 | 값 | PDF 권장값 | 비고 |
|---|---|---|---|---|
| Agent Loop | 모델 호출 최대 턴 | 8 | 8 | |
| Agent Loop | 작업 실행 시간 | 120초 | 120초 | 승인·PR 반영 대기, 회복 확인 대기(20초)는 빼고 구간별로 잰다 (`agent.task_budget`) |
| Agent Loop | 같은 도구 호출 | Node마다 3회 | 3회 | |
| Tools | 도구 결과 길이 | 4,000자 | 길이 제한 | |
| Context | 대화 예산 | 24,000자 | Token 예산 | 넘으면 오래된 도구 결과를 줄인다. 모델 요약은 TODO |
| Recovery | 429·5xx 재시도 | 2회 | 2회 | 이후 대체 모델(`FALLBACK_MODEL`) |
| Recovery | Kafka 작업 재시도 | 3회 | — | 이후 `tasks.dlt`, 작업은 FAILED |
| Recovery | 모델 호출 타임아웃 | 30초 | — | 타임아웃도 재시도 대상이라 한 턴이 최대 90초까지 걸린다 |
| Agent Loop | 추론 강도 | low (propose_action만 medium) | — | `LLM_REASONING_EFFORT`. 비우면 모델 기본값. propose는 `harness.PROPOSE_REASONING_EFFORT` |

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

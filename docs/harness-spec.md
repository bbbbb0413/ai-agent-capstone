# Harness 명세

PDF 표 6-3의 권장값을 기준으로 둔 값과, 팀이 바꾼 값과 이유를 기록한다. 코드는 `server/python/agent/src/agent/harness.py`에 있다.

## 현재 값

| 구성 요소 | 항목 | 값 | PDF 권장값 | 비고 |
|---|---|---|---|---|
| Agent Loop | 모델 호출 최대 턴 | 8 | 8 | |
| Agent Loop | 작업 실행 시간 | 120초 | 120초 | 승인 대기 시간은 빼고 구간별로 잰다 (`agent.task_budget`) |
| Agent Loop | 같은 도구 호출 | 3회 | 3회 | |
| Tools | 도구 결과 길이 | 4,000자 | 길이 제한 | |
| Context | 대화 예산 | 24,000자 | Token 예산 | 넘으면 오래된 도구 결과를 줄인다. 모델 요약은 TODO |
| Recovery | 429·5xx 재시도 | 2회 | 2회 | 이후 대체 모델(`FALLBACK_MODEL`) |
| Recovery | Kafka 작업 재시도 | 3회 | — | 이후 `tasks.dlt`, 작업은 FAILED |

## 변경 이력

| 날짜 | 항목 | 이전 → 이후 | 이유 | 평가 결과 |
|---|---|---|---|---|
| | | | | |

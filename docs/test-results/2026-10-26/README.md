# E2E 검증 결과 — 전체 18개 시나리오 모두 통과

실행: **2026-10-26 10:10:03~10:20:17 KST**, 약 **10분 14초**.
테스트 대상 소스: [`1e3be40`](https://github.com/bbbbb0413/ai-agent-capstone/tree/1e3be406823451c38f49d2e5ab26ed11b9b31967).

**동일 환경에서 전체 18개를 한 번에 재실행한 결과다. 과거 실행 결과를 합산하지 않았다.**

| 구분 | 이번 결과 | 집계 범위 |
|---|---:|---|
| E2E 시나리오 | **18/18 통과** | 실패 0, 미완료 0 |
| 시나리오 세부 검사 | **148/148 통과** | 아래 18개 시나리오에 포함된 판정 항목 |
| 사전 점검 | **11/11 통과** | 시나리오 수·148개 검사와 별도 |
| 복합 장애 | **3/3 통과, 세부 검사 26/26** | 전체 18개·148개에 포함된 부분집합 |

기존 자동 테스트 **340개**(Agent 130, Indexer 35, RAG 8, Backend 129, Frontend 38)는 별도 실행의 단위·DB·프론트 검사 집계다. **이번 E2E 실행에서 340개를 다시 실행한 것은 아니며, 148개와 합산하지 않는다.** 해당 자동 테스트의 실행 로그와 JUnit XML, 제보·첨부·권한·결함 검사의 별도 기록은 [보충 검증 증거](supplemental/README.md)에 첨부했다. 기존 로컬 기록을 공개한 것이며 새 재실행 결과가 아니다.

## 18개 시나리오별 결과

| 키 | 시나리오 | 판정 | 세부 검사 | 시간 | 증거 |
|---|---|---|---:|---:|---|
| `isolation` | 고객사 격리 | 통과 | 8/8 | 3초 | [상세](isolation.md) |
| `guard` | Backend 계약·구조 방어선 | 통과 | 5/5 | 134초 | [상세](guard.md) |
| `question` | 문서 질문·RAG 출처 | 통과 | 4/4 | 4초 | [상세](question.md) |
| `no-issue` | 열린 장애 없는 일반 질문 | 통과 | 7/7 | 4초 | [상세](no-issue.md) |
| `documents` | 문서 업로드·색인·검색·복원·권한 | 통과 | 14/14 | 11초 | [상세](documents.md) |
| `a-pool` | A사 잘못된 배포·GitOps 롤백 | 통과 | 9/9 | 231초 | [상세](a-pool.md) |
| `a-hang` | A사 파드 멈춤·순차 재시작 | 통과 | 11/11 | 162초 | [상세](a-hang.md) |
| `a-surge` | A사 트래픽 급증·HPA 최대치 확장 | 통과 | 9/9 | 164초 | [상세](a-surge.md) |
| `a-surge-max` | A사 안전 상한 초과·미회복 이관 | 통과 | 11/11 | 190초 | [상세](a-surge-max.md) |
| `a-latency` | A사 외부 결제 지연·공지 | 통과 | 6/6 | 76초 | [상세](a-latency.md) |
| `a-dbslow` | A사 DB 지연·DBA 이관 | 통과 | 8/8 | 115초 | [상세](a-dbslow.md) |
| `b-hang` | B사 파드 멈춤·재시작 | 통과 | 8/8 | 173초 | [상세](b-hang.md) |
| `b-latency` | B사 외부 결제 지연·공지 | 통과 | 6/6 | 84초 | [상세](b-latency.md) |
| `b-pool` | B사 롤백 계약 제한·이관 | 통과 | 8/8 | 102초 | [상세](b-pool.md) |
| `b-surge` | B사 확장 계약 제한·이관 | 통과 | 8/8 | 167초 | [상세](b-surge.md) |
| `b-hang-latency` | B사 파드 멈춤 + 결제 지연 | 통과 | 9/9 | 165초 | [상세](b-hang-latency.md) |
| `a-dbslow-backlog` | A사 DB 지연 + 트래픽 급증 | 통과 | 8/8 | 84초 | [상세](a-dbslow-backlog.md) |
| `b-pool-dbslow` | B사 잘못된 배포 + DB 지연 | 통과 | 9/9 | 142초 | [상세](b-pool-dbslow.md) |

공통 4개를 먼저 실행한 뒤 서비스별 5개 묶음을 병렬 실행했다. 같은 서비스 안에서는 순서대로 진행했다. 위 시간은 실행기가 기록한 각 시나리오 소요 시간이며, 일부 사전 복원 대기와 실행 종료 정리는 별도다. 병렬 실행이므로 행별 시간을 합산한 값은 전체 실행 시간과 다르다.

## 이전 공지 실패와 이번 결과

이전 누적 결과는 17개 통과·1개 실패였고, `b-hang-latency`에서 공지 함수에 허용되지 않는 `service:null`이 전달됐다. [이전 결과와 실패 증거](https://github.com/bbbbb0413/ai-agent-capstone/blob/1e3be406823451c38f49d2e5ab26ed11b9b31967/docs/test-results/2026-10-26/README.md)는 Git 이력에 남아 있다.

이번에는 해당 공지 승인 요청의 인자가 `incident_id`, `message`뿐이었다. 작업 응답에서 `notice_result.status=SENT`를 확인했고, 실제 `NOTICE_SENT` 감사 기록도 있다. 재시작, RB-STD-02에서 RB-STD-04로 재진단, 공지 저장의 9개 검사가 모두 통과했다. [작업 응답](b-hang-latency_task.json), [실행 이벤트](b-hang-latency_events.json), [감사 기록](action_notice_audit.json).

**제품 코드 수정 없이 통과한 재실행 결과이므로, 과거 공지 인자 결함의 근본적인 수정 완료를 의미하지 않는다.** p95 결측 입력, 무관 revision·Degraded 상태의 동기화, 최종 제안 검사, Markdown 진단 파싱, 문서 실패 단계 UI의 별도 결함 검사를 다시 실행하지 않았다. 전체 E2E 통과는 TC-01~12의 모든 세부 인수조건 충족이나 전체 보고서 정확도를 보증하지 않는다.

## 실행 환경

- macOS Docker Compose 프로젝트 `opslens`, kind `opslens-tenants`, ArgoCD `tenant-a`.
- 새 다운로드 소스와 기존 실행 환경의 제품 소스 및 `scripts/e2e.py`, `scripts/inject.py`가 동일함을 확인하고 기존 스택을 재사용했다. 전체 컨테이너 이미지를 새로 빌드한 실행은 아니다.
- 캐시 이미지의 의존성과 소스·프롬프트 마운트, 기존 소스에서 빌드한 Backend JAR·Frontend dist를 사용했다. [소스·산출물 해시](source_manifest.json), [환경 기록](environment.json).
- 실제 Prometheus/Loki, Kafka, PostgreSQL, MCP, 모델 API, 장애 주입, 승인, Kubernetes 재시작, GitOps PR 생성·병합·동기화를 사용했다.
- 모델 설정은 `gpt-5-mini`와 대체 `gpt-5-nano`이고 Jev는 비활성화했다. 제품이 호출별 응답 모델 메타데이터를 보존하지 않으므로 설정 모델을 각 호출의 실제 실행 모델로 단정하지 않는다. [모델 설정](model_environment.json).
- 고객 공지는 포털 저장이다. 외부 Slack 발송은 수행하지 않았고, Slack 발송 레코드는 실행 전후 모두 0개다.
- 결과 파일의 `127.0.0.1:18000` 작업 링크는 해당 로컬 실행 환경에서만 열린다. 생성된 보고서 원문은 실행 증거이며 문장·단위의 정확성 전체를 검증한 결과는 아니다.

## 종료 후 복원 확인

Backend·ArgoCD 기준을 `main`으로 복원했고, ArgoCD는 `Synced / Healthy`다. 테스트 임시 브랜치와 조치 PR 브랜치를 삭제했으며, 테스트 PR [#94](https://github.com/bbbbb0413/ai-agent-capstone/pull/94)·[#95](https://github.com/bbbbb0413/ai-agent-capstone/pull/95)·[#96](https://github.com/bbbbb0413/ai-agent-capstone/pull/96)은 병합된 이력으로 남는다. 테스트 중 변경한 것은 임시 브랜치이며 `main` 커밋은 실행 전후 `1e3be40`으로 동일했다.

최종 확인: 열린 인시던트 0, 미완료 작업 0, 실행 중인 8개 데모 파드 모두 장애 없음·풀 크기 20, 의존 장애 ConfigMap 해제, 부하 발생기 0, A사 HPA 최대 3·현재 레플리카 2. `a-surge-max` 정리 중 종료되는 파드에 대한 연결 경고 1회가 있었으나, 종료 후 각 파드의 실제 상태를 별도로 확인해 정상 복원을 확인했다. [복원 확인 원본](cleanup_verification.json).

## 재현 방법

Compose·kind·문서 색인·모델 API·GitHub 접근을 준비한 표준 환경에서:

```bash
python3 scripts/e2e.py
```

이번 환경은 Backend `18080`, Frontend `18000` 포트를 사용했다. 실행 래퍼는 `e2e.API`, `e2e.WEB` 및 `e2e.compose`·`inject.in_backend`의 환경 연결만 기존 컨테이너로 지정하고, 기존 `e2e.main()`을 시나리오 인자 없이 호출했다. 결과 수집용 `Run` 확장은 판정 이후 JSON·작업·이벤트를 추가 저장했다. 제품·장애 주입·승인·판정 코드는 수정하지 않았다. 기존 [복합 장애 전용 실행기](../../../scripts/run_compound_e2e.py)는 3개만 실행하므로 전체 18개 재현 명령과 구분한다.

## 결과 파일

- [실행기 요약](summary.md), [전체 실행 로그](full_run.log)
- [전체 판정 JSON](results.json), [복합 장애 판정 JSON](compound_results.json)
- 시나리오별 `.md`·`.log`: 판정과 진행 기록
- 작업이 있는 시나리오의 `_task.json`·`_events.json`: 전체 작업 응답과 실행·승인 이벤트
- [조치·공지 감사 기록](action_notice_audit.json), [종료 상태](cleanup_verification.json)

기존 누적 결과 JSON과 과거 실행 로그는 이번 전체 실행 결과로 대체했다. 이전 실행 원본은 위 Git 이력 링크에서 확인할 수 있다.


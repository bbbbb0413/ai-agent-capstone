# 별도 자동 테스트와 인수조건 검증 증거

2026-10-26에 실행하고 로컬에 보관했던 검증 기록을 첨부한다. 이번 첨부 과정에서 테스트를 새로 실행하지 않았다. 제품 코드도 변경하지 않았다.

[전체 E2E 결과](../README.md)의 **18/18 시나리오·148/148 검사**와 이 폴더의 **자동 테스트 340개**는 서로 다른 실행이다. 합산하지 않는다. 아래 결함 재현 검사도 340개에 포함되지 않는다.

## 자동 테스트와 빌드

| 대상 | 통과 | 실패 / 건너뜀 | 실행 증거 |
|---|---:|---|---|
| Agent | 130 | 0 / 0 | [실행 로그](agent_tests.log) |
| Indexer + PostgreSQL/pgvector | 35 | 0 / 0 | [실행 로그](indexer_tests_with_vector.log) |
| RAG + PostgreSQL/pgvector | 8 | 0 / 0 | [실행 로그](rag_tests_with_vector.log) |
| Backend | 129 | 0 / 0 | [실행 로그](backend_tests_final.log), [JUnit XML 25개](backend_test_results/) |
| Frontend | 38 | 0 / 0 | [실행 로그](frontend_tests.log) |
| 합계 | **340** | **0 / 0** | Backend 수치는 JUnit XML의 tests/failures/errors/skipped를 합산했다 |

[프론트 배포 빌드 로그](frontend_build.log)도 첨부한다. Backend 로그는 test 실행 기록이며 JAR 빌드 로그로 해석하지 않는다. 경고나 테스트가 의도적으로 출력한 오류 메시지는 삭제하지 않았다.

## PPT 인수조건과 별도 실행

| 연결 항목 | 확인한 내용 | 원본 기록 |
|---|---|---|
| TC-01 | 동일 알림 3회 수신 후 열린 인시던트 1개·진단 작업 1개 | [HTTP 검사](http_results.json) |
| TC-06·07 | 엔지니어 공지 승인 403, B사 롤백·확장 422 | [HTTP 검사](http_results.json) |
| 제보 사례 9 | 증상 표현 3종 모두 등록 승인 대기, REJECT 202 및 거절 이력 | [제보 실행 결과](symptom_live_results.json) |
| TC-10·첨부 사례 10 | 용량 초과 거부, 로그·PNG 분석 DONE, 파일명·L1~L3 근거, 변경 도구 미호출 | [첨부 실행 결과](attachment_live_results.json), [합성 로그](synthetic_error.log), [합성 PNG](synthetic_error.png) |
| TC-09 | 색인 실패 PARSE·사유 기록, 이전 검색 버전 v4 유지 | [실패·검색 결과](sla_failure_live_results.json), [당시 문서 화면](document_failure_ui.jpg) |
| TC-12 | B사 토큰으로 A사 원문 거부, B사·공통 원문 접근 및 검색 | [RAG 실제 경로 검사](rag_live_results.json), [HTTP 검사](http_results.json) |
| TC-11 | Slack 발송·중복·불명확 상태의 DB 검사 | [SlackDeliveryDatabaseTest](backend_test_results/TEST-com.opslens.backend.slack.SlackDeliveryDatabaseTest.xml), [SlackSafetyTest](backend_test_results/TEST-com.opslens.backend.slack.SlackSafetyTest.xml) |

외부 Slack 실제 발송은 이번 첨부를 위해 수행하지 않았다. 과거 실제 발송·수신 확인은 [2026-10-22 기록](../../../testing.md#관리자-설정과-검토-후-slack-발송-2026-10-22)을 참고한다. 제보 등록 거절 검사는 재시작 거절 TC-04의 증거가 아니다. 첨부 결과의 DONE·근거 표시는 답변 전체의 정확도 검증을 의미하지 않는다.

TC-09 화면에는 실패 사유와 이전 검색 버전이 보이지만 `indexStage=PARSE`는 표시되지 않는다. 따라서 API·검색 검사의 성공과 화면 인수조건 실패를 구분한다.

## 미해결 결함과 경계값 검사

- [경계값·파싱·최종 제안 검사 결과](boundary_results.json), [검사 스크립트](boundary_probes.py)
  - TC-05: 정상 입력 및 임계값 경계는 기대와 일치한다. p95 null·누락은 회복 True로 잘못 판정한다. 음수 입력도 True이며 문자열 p95는 TypeError다.
  - TC-08: dependency_share 0.49 / 0.50 / 0.51은 각각 RB-STD-03 / 05 / 05로 판정한다.
  - Markdown의 **처리할 원인:** 형식에서 처리 대상이 null이다.
  - 부적합 제안이 마지막에 반환되는 상태를 재현했다. 시작 턴 0에서는 모델 3회·guard 2회, 시작 턴 7에서는 모델 1회·guard 1회다.
- GitOps 동기화: 무관 revision 또는 Degraded health에서 작업 재개를 차단해야 한다는 검사 **2건이 모두 실패**했다.
  - [실행 로그](gitops_probe.log), [JUnit 결과](gitops_probe_results.xml), [mock 컨트롤러 검사 코드](probes/com/opslens/backend/change/GitOpsSyncAuditTest.java)

위 결함 재현 기록은 실패를 숨기지 않고 별도로 보존한 것이다. 전체 E2E 통과 또는 기존 자동 테스트 340개 통과만으로 이 결함들이 수정되었다고 판단하지 않는다. TC-01~12 전체 인수조건을 이번에 재평가하지 않았다.

## 증거 보존 범위

- 실행 로그·JSON·JUnit XML·스크린샷은 기존 로컬 기록에서 가져왔다.
- 공개용 텍스트에서 개인 홈 경로는 `/LOCAL_HOME`, JUnit 호스트명은 `LOCAL_HOST`로 치환했다. 테스트 수치·판정·작업 응답은 유지했다. 이미지와 합성 입력은 원본 바이트를 유지했다.
- [manifest.json](manifest.json)에 파일별 원본 SHA-256, 공개본 SHA-256 및 치환 내역을 기록했다. 실행 환경 설정·키 입력 파일·자격증명 파일은 첨부하지 않았다.
- 공개 기준 저장소 커밋은 `632ae8c`다. 이는 첨부 시점의 비교 기준이며 별도 테스트의 새로운 실행 커밋을 의미하지 않는다.
- PPT의 로컬 로그 경로 대신 이 폴더의 GitHub 링크를 증거 링크로 사용할 수 있다.


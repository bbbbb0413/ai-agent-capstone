# 문서 지도

| 문서 | 내용 | 언제 고치나 |
|---|---|---|
| [OpsLens-기획안.md](OpsLens-기획안.md) | 기준 기획: 목표, 아키텍처, Agent·MCP·Harness 설계, 데모 시나리오 | 설계가 바뀔 때 |
| [adr/](adr/) | 결정 기록 (ADR). 무엇을 왜 골랐고 무엇을 버렸는지 | 되돌리기 어려운 결정을 할 때 새 번호로 추가 |
| [specs/](specs/) | 구현 전·진행 중인 기능 스펙과 계획 (`SPEC-<기능>.md`) | 기능을 만들기 전에 쓰고, 구현하며 갱신 |
| [harness-spec.md](harness-spec.md) | Harness 현재 값과 변경 이력(값, 이유, 평가 결과) | Harness 값이나 판정 방식을 바꿀 때 (코드 주석이 이 경로를 참조한다) |
| [기능별-변경사항.md](기능별-변경사항.md) | 기획 이후 바뀐 것을 기능 영역별로 정리한 기록 | 기능을 고친 뒤 |
| [testing.md](testing.md) | 테스트 종류와 실행 방법 (E2E, 단위·DB 회귀) | 테스트를 추가하거나 실행 방법이 바뀔 때 |

## 현재 스펙

| 스펙 | 상태 |
|---|---|
| [SPEC-chat-history.md](specs/SPEC-chat-history.md) | 승인 대기: 사용자별 채팅 기록과 SSE 재연결 |
| [SPEC-fault-based-actions.md](specs/SPEC-fault-based-actions.md) | 승인됨 (구현 전): 장애 형태·시스템 구조 기반 조치 (공통 런북, 서비스 카탈로그, 실행 플랫폼, 교차 매트릭스 E2E) |

## 다른 곳에 있는 문서

- `gitops/README.md`: 고객사 환경(kind, ArgoCD) 구성과 실행
- `data/documents/`: RAG가 색인하는 데모 런북·계약서 (Agent의 근거 문서)
- `server/python/agent/prompts/`: Agent 단계별 지시문

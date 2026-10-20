# ADR 0001: 고객사 인프라 조치와 혼합 실행 환경 (compose + Kubernetes)

- 상태: **채택됨** (D1~D6 2026-10-19, D7~D9 2026-10-20 추가. 코드 반영 완료)
- 날짜: 2026-10-19 (개정 2026-10-20)
- 관련 문서: [기획안](../OpsLens-기획안.md) 3장·4.4절·10장, [SPEC-fault-based-actions](../specs/SPEC-fault-based-actions.md)

이 문서는 "OpsLens가 고객사 장애를 어떻게 조치하는가"를 정한 과정과 근거를 남긴다. 논의는 질문 하나에서 시작해 여섯 가지 결정(D1~D6)으로 이어졌고, 구현하면서 "무엇을 근거로 어떤 조치를 고르는가"에 대한 결정 세 가지(D7~D9)를 더했다.

## 1. 배경: 무엇이 문제였나

### 1.1 처음 구현한 조치는 앱 내부 시뮬레이션뿐이었다

v2 기획안과 첫 구현(커밋 `85d478d`)의 조치는 다음 다섯 가지였다.

| 조치 | 실제로 일어나는 일 |
|---|---|
| `adjust_pool` (커넥션 풀 크기) | demo-app 안의 숫자만 바뀐다 |
| `restart` | 실제 재시작이 아니라 장애 상태만 초기화한다 |
| `clear_fault` | 주입한 장애를 끈다 |
| `escalate`, `notice` | 보내지 않고 감사 로그에만 남긴다 |

장애 시나리오(DB 커넥션 고갈, 외부 결제 API 지연)도 모두 애플리케이션 수준이었다. 클라우드나 Kubernetes 같은 인프라 조치는 하나도 없었다.

### 1.2 시장의 표준 조치는 인프라 계층이다

비슷한 서비스가 어디까지 조치하는지 조사했다(2026-10-19). 업체 자료가 대부분이라 수치는 검증되지 않았다.

| 조치 수준 | 서비스 | 하는 일 |
|---|---|---|
| 진단·권고만 | [AWS DevOps Agent](https://aws.amazon.com/devops-agent/features) | 원인 분석과 완화 계획 작성. [실행은 사람이 한다](https://stakpak.dev/blog/2026/04/14/aws-devops-agent-review-can-it-actually-fix-your-infrastructure/) |
| 정해진 인프라 조치 실행 | [Komodor Klaudia](https://help.komodor.com/hc/en-us/articles/32313630528786-Self-Healing) | 멈춘 파드 재시작, 실패한 배포 롤백. 권한 정책 안에서 실행 |
| 런북 기반 조치 | [PagerDuty SRE Agent](https://www.businesswire.com/news/home/20251008279706/en) | 진단·조치 권고와 실행, [클라우드 Agent 연동 자동 복구](https://s206.q4cdn.com/635206389/files/doc_news/PagerDuty-Expands-AI-Ecosystem-to-Supercharge-AI-Agents-and-Deliver-Autonomous-Operations-2026.pdf) |
| 코드 수정안 | [Rollbar Resolve](https://cdn.rollbar.com/resolve) | 운영 오류를 테스트가 포함된 PR로 만든다 |
| MSP 기기 관리 | [NinjaOne](https://www.ninjaone.com/rmm/self-healing-automation/), [ConnectWise](https://www.connectwise.com/blog/agentic-ai-use-cases-for-msps-and-it-teams) | PC·서버의 멈춘 서비스, 누락된 재부팅 등을 스크립트로 복구 |
| 국내 MSP | [베스핀글로벌 MSP 코파일럿](https://www.inews24.com/view/1823409) | 클라우드 운영 자동화. 장애 자동 조치의 범위는 공개되지 않았다 |

정리하면 다음과 같다.

- 실제로 고쳐주는 곳은 **인프라 계층**(파드 재시작, 배포 롤백, 확장)에서 **되돌릴 수 있는 정해진 조치**만 실행한다.
- 애플리케이션 문제는 직접 고치지 않고 **PR로 제안**한다.
- 앱 내부 값을 바꾸는 OpsLens의 조치는 실제 서비스에서 거의 쓰지 않는 방식이었다.
- 위 서비스들은 단일 조직용이거나 기기 관리용이다. **고객사마다 다른 계약과 운영 방식에 맞춰 조치하는 것**은 다루지 않는다. OpsLens의 차별점은 여기서 유지된다.

## 2. 결정

### D1. 시나리오에 인프라 조치를 넣는다

파드 재시작, 레플리카 확장, 배포 롤백을 시나리오의 중심 조치로 삼는다. 앱 내부 값 조정은 뺀다.

### D2. CLI가 아니라 API/SDK와 고객사별 최소 권한으로 조치한다

AWS CLI나 `gcloud`, `kubectl`을 셸로 실행하는 방식은 쓰지 않는다.

- 명령 문자열을 조립해야 해서 인자 주입 위험이 있고, 결과를 텍스트로 파싱해야 한다.
- 실행 서버에 로그인된 넓은 자격증명을 그대로 쓰게 된다.

대신 SDK나 API를 쓰고, **고객사마다 그 조치에 필요한 권한만 가진 자격증명**을 쓴다.

- AWS라면 고객사 계정마다 역할(IAM Role)을 두고, 조치할 때만 임시 자격증명을 받는다(AssumeRole).
- 데모에서는 Kubernetes 네임스페이스마다 ServiceAccount와 Role을 두는 것으로 같은 구조를 재현한다.

### D3. GitOps 고객사의 "원하는 상태 변경"은 Git PR로 반영한다

ArgoCD나 Flux를 쓰는 환경에서는 Git에 적힌 상태가 정답이다. Agent가 API로 레플리카를 바꿔도 ArgoCD가 Git 상태로 되돌린다(self-heal). 되돌리지 않게 설정했다면 Git과 실제가 어긋난다(drift). 그래서 조치를 성격별로 나눈다.

| 조치 종류 | 예 | GitOps 고객사 | 직접 운영 고객사 |
|---|---|---|---|
| 원하는 상태를 바꾸지 않는 일회성 조치 | 파드 재시작, 캐시 비우기, DB 장애 조치 실행 | API로 바로 실행 | API로 바로 실행 |
| 원하는 상태를 바꾸는 조치 | 레플리카 수, 이미지 롤백, 리소스 한도, 설정값 | **설정 저장소에 PR** → 병합 → 동기화 | API로 바로 실행 |
| 클라우드 리소스 변경 (IaC 관리) | 인스턴스 크기, 오토스케일링 한도 | **IaC 저장소에 PR** → plan 확인 → 적용 | SDK로 실행 |

GitOps 고객사에서는 **PR 리뷰와 병합이 곧 사람의 승인**이 된다. 긴급 상황용 break-glass(직접 조치 후 Git을 맞추는 PR을 반드시 뒤따라 올림)는 설계에만 두고 데모에서는 다루지 않는다.

### D4. "무엇을 할지"는 Agent가, "어떻게 반영할지"는 backend가 고객사별로 정한다

고객사 프로필에 **변경 방식**(`GITOPS` 또는 `DIRECT`)을 둔다. Agent는 "레플리카를 6으로" 같은 조치 의도만 정하고, Spring Boot가 계약 허용 여부와 변경 방식을 보고 반영 경로를 고른다.

```
Agent: 조치 의도 결정 (예: scale_deployment order-api 6)
  → business-mcp → Spring Boot /internal/tenants/{tenant}/actions
      ├─ 계약상 허용된 조치인가 (기존 규칙)
      ├─ GITOPS: 설정 저장소에 PR 생성 → 병합·동기화 완료 알림 → Agent 재개
      └─ DIRECT: 고객사 자격증명으로 Kubernetes API 호출
```

- 기존 원칙 "업무 규칙은 Spring Boot 한 곳에만"과 맞는다.
- Agent와 MCP 도구는 반영 방식이 늘어나도 바뀌지 않는다. 클라우드 어댑터는 Spring Boot에만 추가된다.
- 고객사 자격증명은 Spring Boot만 가진다. Agent는 자격증명을 보지 못한다.

### D5. OpsLens는 docker compose로 유지하고, 고객사 환경만 Kubernetes로 옮긴다

"OpsLens를 어디에 띄우는가"와 "고객사 환경을 무엇으로 흉내 내는가"를 나눠서 판단했다.

| 구분 | 실행 환경 |
|---|---|
| OpsLens 플랫폼 (frontend, backend, agent, MCP, DB, Kafka, 관측 스택) | **docker compose** (지금 그대로) |
| 고객사 환경 (A사·B사 서비스, ArgoCD, 설정 저장소) | **Kubernetes (kind)** |

근거:

1. **현실과 구조가 같다.** 실제 MSP의 운영 플랫폼과 고객사 클러스터는 서로 다른 곳에 있다. OpsLens가 고객사 클러스터 안에 있을 이유가 없다.
2. **과제 요구사항과 맞는다.** 강의 자료의 레퍼런스 아키텍처는 docker compose 배포와 docker network 신뢰 경계를 기준으로 한다. 지금까지의 설계와 검증도 이 기준에 맞춰져 있다.
3. **바뀌는 범위가 작다.** compose에서는 demo-app 두 개만 빠지고 kind로 옮겨간다.

kind도 Docker 컨테이너로 돌기 때문에 같은 Docker 네트워크에 붙일 수 있다. backend는 그 네트워크로 kind의 API 서버를 호출한다.

### D6. 설정 저장소는 별도 저장소가 아니라 이 프로젝트 저장소의 `gitops/` 경로로 둔다

| 경로 | 역할 |
|---|---|
| `gitops/cluster/` | kind 설정, 고객사 네임스페이스·권한, 관측 수집기, ArgoCD Application |
| `gitops/tenants/tenant-a/` | A사의 원하는 상태. ArgoCD가 GitHub `main`의 이 경로만 동기화한다 |
| `gitops/tenants/tenant-b/` | B사 초기 배포. ArgoCD는 보지 않는다 |

근거:

1. **과제 제출물이 하나로 모인다.** 평가자가 저장소 하나만 받아도 플랫폼 코드와 고객사 환경 정의를 함께 본다.
2. **Gitea 같은 Git 서버를 하나 더 띄우지 않는다.** PC 자원 부담(4장)이 줄고, PR은 GitHub API로 그대로 만든다.
3. **현실과의 차이는 경로 제한으로 메운다.** 실제로는 고객사마다 저장소가 따로 있다. 대신 backend가 PR의 변경 경로를 `gitops/tenants/<tenant>/`로 제한하고, ArgoCD Application도 그 경로만 본다. 고객사 저장소를 따로 두는 구조로 바꿀 때는 Application의 `repoURL`과 backend 설정만 바꾸면 된다.

대가:

- 시연 중 생기는 장애 커밋과 롤백 PR이 이 저장소에 남는다. E2E는 실행마다 임시 브랜치(`e2e/<시각>`)로 A사 기준 브랜치를 바꿔(backend `tenants.gitops_branch`, ArgoCD `targetRevision`) `main`에는 남기지 않고, 끝나면 브랜치를 지운다(SPEC-fault-based-actions 5.8). PR 기록은 닫힌 PR로 남는다.
- ArgoCD는 GitHub에 푸시된 내용만 본다. `gitops/tenants/tenant-a/`를 로컬에서만 고치면 클러스터에 반영되지 않는다.

### D7. 조치 근거는 MSP 공통 런북이고, 고객사 문서는 계약과 서비스별 절차만 둔다

처음에는 고객사 런북에 "커넥션 풀 고갈이면 롤백" 같은 조치 규칙을 고객사마다 따로 적었다. 같은 장애 형태인데 고객사마다 문장이 달라 서로 어긋났고, Agent가 다른 고객사에서는 같은 장애에 다른 조치를 골랐다.

- 장애 형태별 증상·구분 기준·조치는 **공통 런북**(`data/documents/_common/runbook-standard.md`, RB-STD-01~07)에 한 번만 쓴다. 모든 고객사 작업에서 검색된다.
- 고객사 문서에는 **계약**(허용 조치, 승인 절차, 공지 기한)과 **서비스별 절차·주의**(예: "DB 이상이면 A사 DBA에게 먼저 연락", "단일 레플리카라 재시작하면 전체 중단")만 둔다.
- Agent는 진단 근거(로그 신호, 에러율, 직전 변경)로 RB-STD 조항 하나를 고르고, 그 조항의 조치를 후보로 삼는다. 제안 이유에 조항 번호를 넣는다.

### D8. 시스템 구조는 서비스 카탈로그에 두고, 구조 제약은 backend가 계산·검사한다

"레플리카를 몇까지 늘려도 되는가", "재시작하면 서비스가 멈추는가"는 숫자로 정해지는 사실이다. 이것을 런북 문장으로 두면 RAG가 틀린 값을 가져오거나 Agent가 계산을 틀린다.

- 구조 사실(실행 플랫폼, 의존 대상과 소유자, 최대 연결 수, 풀 크기, 레플리카 범위, 상태 저장 여부)은 backend DB의 **서비스 카탈로그**에 둔다.
- backend가 계산값을 함께 돌려준다: 안전 상한(`safeMaxReplicas` = min(계약 상한, ⌊DB 최대 연결 × 0.8 ÷ 풀 크기⌋)), 무중단 재시작 가능 여부, 할 수 있는 조치(`supportedActions` = 플랫폼 지원 ∩ 계약 허용).
- backend는 조치 요청을 받을 때 같은 값으로 다시 검사해 422로 거부한다. Agent가 잘못 제안해도 실행되지 않는다.
- 실행 플랫폼(`runtime`)은 카탈로그 항목이고, backend가 그 값으로 어댑터를 고른다. 지금은 Kubernetes만 구현했고 Docker 단독·VM·ECS는 확장 지점으로만 둔다.
- 할 수 없는 조치가 필요하면 다른 조치로 대신하지 않고 에스컬레이션한다(`ACTION_BLOCKED`, 막힌 이유 `CONTRACT`·`STRUCTURE`·`RUNTIME`). 에스컬레이션은 받는 사람과 메시지를 정해 발송 대기열에 넣는 데까지 한다(실제 발송은 하지 않는다).

### D9. 트래픽 급증은 HPA가 대응하고, Agent는 HPA 최대치가 모자랄 때만 최대치를 올린다

A사 `order-api`에 HPA(최소 2, 최대 3, CPU 목표 100%)를 두었다. Agent가 HPA가 관리하는 Deployment의 레플리카를 직접 바꾸면 HPA가 곧 덮어쓰고, GitOps 매니페스트의 `replicas`와도 다툰다.

| 상황 | Agent |
|---|---|
| HPA가 최대치 아래에서 늘리는 중 | 조치하지 않는다 |
| HPA가 최대치이고 최대치 < 안전 상한 | `adjust_autoscaler`로 최대치를 안전 상한(4)까지 올린다 (A사는 `hpa.yaml` PR) |
| HPA가 최대치이고 최대치 = 안전 상한 | 에스컬레이션 (`ACTION_BLOCKED`, `STRUCTURE`). 더 올리면 DB 연결이 모자란다 |

- HPA가 관리하는 서비스의 `scale_deployment`는 backend가 거부한다. 최소치는 바꾸지 않는다(사전 확장·용량 계획은 장애 대응 밖이다).
- 부하가 빠지면 HPA가 스스로 줄인다. 올린 최대치를 되돌릴지는 A사가 정한다.

## 3. 검토했지만 택하지 않은 안

| 안 | 택하지 않은 이유 |
|---|---|
| 앱 내부 조치만 유지 | 시장 기준으로 현실감이 없다. "실제로 어디까지 고쳐주나"라는 질문에 답하기 어렵다 |
| CLI로 조치 | D2의 보안·안정성 문제 |
| GitOps 고객사에도 API로 직접 조치 | 동기화가 되돌리거나 drift가 남는다 (D3) |
| OpsLens까지 전부 Kubernetes로 이전 | 레퍼런스의 compose 배포·신뢰 경계를 다시 설계하고 검증해야 한다. 평가 점수에 더해지는 부분은 없고 작업량만 커진다 |
| 실제 AWS·GCP로 데모 | 비용, 계정 관리, 시연 안정성 부담이 크다. 같은 구조에 클라우드 어댑터만 추가하면 된다는 설계 설명으로 대신한다 |
| 조치 근거를 고객사 런북마다 쓰기 (D7) | 같은 장애 형태의 조치가 고객사마다 어긋난다 |
| 구조 사실을 런북 문장으로 두기 (D8) | 값이 부정확하고 backend가 검사할 수 없다 |
| 조치를 코드 규칙으로 고정하기 (D8) | Agent의 판단(장애 형태 분류, 병목 위치)이 없어진다. 코드는 판단의 결과를 검사만 한다 |
| Agent가 HPA 대상의 레플리카를 직접 바꾸기 (D9) | HPA와 GitOps가 덮어쓴다 |

## 4. 결과와 영향

### 좋아지는 점

- 조치가 시장 표준(재시작·확장·롤백)과 같아져 현실감이 생긴다.
- 고객사별 변경 방식과 고객사별 자격증명이 **MSP라는 문제 설정의 차별점을 더 분명하게** 만든다.
- GitOps 고객사에서는 PR 병합이 승인 절차가 되어 감사 기록이 Git에도 남는다.

### 비용과 새 위험

| 항목 | 내용 | 대응 |
|---|---|---|
| PC 자원 | kind, ArgoCD, 설정 저장소가 추가된다. 현재 Docker 메모리는 8GB | 측정 결과 A사 2~4개·B사 1개 파드와 부하 발생기까지 동작한다. 파드 메모리 한도는 512Mi |
| 반영 지연 | PR 병합과 동기화에 몇 분이 걸린다 | 병합 대기를 승인 대기처럼 interrupt로 다룬다. 실행 시간 한도(120초)에 넣지 않는다 |
| 구성 복잡도 | 네트워크 연결, 고객사 자격증명 관리, 클러스터 안 관측 수집이 새로 생긴다 | 단계별로 나눠 검증한다(기획안 16장) |
| 기존 코드 | demo-app의 관리 API(`/admin/**`)와 `adjust_pool` 조치가 바뀐다 | 시나리오 주입은 매니페스트·이미지 버전 변경으로 옮긴다 |

### 아직 정하지 않은 것

- ~~설정 저장소 위치~~ → D6에서 결정
- 클러스터 하나에 네임스페이스로 고객사를 나눌지, 고객사마다 클러스터를 둘지 (기획안은 자원 때문에 클러스터 하나를 가정)
- 진행 중인 채팅 기록 스펙(`docs/specs/SPEC-chat-history.md`)과의 구현 순서

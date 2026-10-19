## 조치 제안 단계

- 진단 결과와 런북 조항을 보고, 이 고객사 계약에서 허용된 조치 중 하나를 도구로 호출한다.
- 런북 조치 이름과 도구 인자는 다음처럼 대응한다. `service`에는 장애가 난 서비스 이름을 넣는다.
  - 파드 재시작: `business_execute_runbook_action(action="restart_pods", service=…)`
  - 레플리카 확장: `business_execute_runbook_action(action="scale_deployment", service=…, replicas=…)`
  - 직전 배포로 롤백: `business_execute_runbook_action(action="rollback_deployment", service=…)`
- 런북에 1차·2차 조치가 나뉘어 있으면 1차 조치부터 제안한다. 런북이 조건(예: 직전 변경 여부)에 따라 조치를 나누면 진단 결과의 근거로 조건을 판단한다.
- 반영 방식(바로 실행 또는 설정 저장소 PR)은 backend가 고객사에 맞춰 정한다. 제안에서 반영 방식을 고르지 않는다.
- 고객 공지는 조치를 대신하지 않는다. 공지는 보고서를 쓴 뒤 따로 승인을 받아 보낸다. 할 수 있는 조치가 있으면 조치를 고른다.
- 런북에 MSP가 고칠 수 없다고 적혀 있으면(외부 의존 지연 등) 조치 대신 고객 공지(`business_send_customer_notice`)를 제안한다.
- 호출과 함께 이유를 두 문장 이내로 적고, 근거가 된 런북 조항 번호를 넣는다. 조항 번호는 위에 주어진 런북·계약 조항에 있는 것만 쓰고, 조항이 비어 있으면 "근거 조항 없음"이라고 적는다. 같은 내용을 `reason` 인자에도 한 문장으로 넣는다.
- 반드시 도구 하나를 호출한다. 계약상 할 수 있는 조치가 없으면 고객 공지를 제안한다.

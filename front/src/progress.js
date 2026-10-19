// 진행 이벤트를 사람이 읽는 타임라인으로 바꾼다 (기획안 5.3: "A사 에러율 조회 중…" 같은 상태 표시)

export const STAGES = {
  load_context: '컨텍스트 수집',
  diagnose: '진단',
  retrieve_runbook: '런북 검색',
  propose_action: '조치',
  verify: '회복 확인',
  escalate: '담당자 호출',
  notice: '고객 공지',
  report: '보고서',
}

export const TOOL_LABELS = {
  business_get_tenant_profile: '고객사 정보 조회',
  business_get_sla_terms: 'SLA 조건 조회',
  business_get_service_inventory: '서비스 구성 조회',
  business_list_incidents: '인시던트 목록 조회',
  business_list_recent_changes: '최근 배포·변경 이력 조회',
  business_execute_runbook_action: '런북 조치 실행',
  business_send_customer_notice: '고객 공지 발송',
  business_escalate_incident: '담당자 호출',
  grafana_query_prometheus: '메트릭 조회',
  grafana_query_prometheus_histogram: '응답 시간 분포 조회',
  grafana_list_prometheus_label_values: '메트릭 라벨 조회',
  grafana_query_loki_logs: '로그 조회',
  grafana_query_loki_patterns: '로그 패턴 분석',
  grafana_query_loki_stats: '로그 통계 조회',
  rag_search_documents: '런북·계약 문서 검색',
}

export const RUNBOOK_ACTIONS = {
  restart_pods: '파드 재시작',
  scale_deployment: '레플리카 확장',
  rollback_deployment: '직전 배포로 롤백',
}

// backend TaskService.ADMIN_ONLY_TOOLS와 같다. 고객에게 나가거나 돈이 걸린 도구는 MSP 관리자만 승인·거절한다
export const ADMIN_ONLY_TOOLS = ['business_send_customer_notice', 'business_confirm_sla_credit']

/** 승인 카드에 보여 줄 인자. 도구별로 사람이 읽는 항목 이름을 붙인다 */
export function approvalFields({ tool, args = {} }) {
  switch (tool) {
    case 'business_execute_runbook_action':
      return [
        ['조치', RUNBOOK_ACTIONS[args.action] || args.action],
        ['대상 서비스', args.service],
        ['레플리카', args.replicas != null ? `${args.replicas}개` : null],
        ['근거', args.reason],
        ['인시던트', args.incident_id != null ? `#${args.incident_id}` : null],
      ].filter(([, value]) => value)
    case 'business_send_customer_notice':
      return [['공지 내용', args.message], ['인시던트', args.incident_id != null ? `#${args.incident_id}` : '연결된 인시던트 없음']]
    case 'business_escalate_incident':
      return [['호출 사유', args.reason], ['인시던트', `#${args.incident_id}`]]
    default:
      return Object.entries(args).map(([key, value]) => [key, typeof value === 'object' ? JSON.stringify(value) : String(value)])
  }
}

/** 승인하면 무엇이 바뀌는가 (기획안 5.3: 승인 카드의 '영향') */
export function approvalImpact({ tool, args = {}, changeMode }) {
  if (tool === 'business_send_customer_notice') return '고객사 담당자에게 위 내용으로 공지가 발송된다. 발송 후에는 취소할 수 없다.'
  if (tool === 'business_escalate_incident') return '고객사 온콜 담당자를 호출한다.'
  if (tool !== 'business_execute_runbook_action') return null
  const service = args.service || '서비스'
  const what = {
    restart_pods: `${service}의 파드를 재시작한다. 재시작하는 동안 처리량이 잠시 줄 수 있다.`,
    scale_deployment: `${service}의 레플리카 수를 ${args.replicas}개로 바꾼다.`,
    rollback_deployment: `${service}를 직전 배포(설정·이미지)로 되돌린다. 그 이후의 변경은 사라진다.`,
  }[args.action] || `${service}에 ${args.action} 조치를 실행한다.`
  const how = changeMode === 'GITOPS'
    ? ' 바로 반영되지 않고 설정 저장소에 PR이 만들어진다. PR이 병합되고 ArgoCD가 동기화하면 반영된다.'
    : changeMode === 'DIRECT' ? ' 승인 즉시 Kubernetes API로 반영된다.' : ''
  return what + how
}

const DECISIONS = { APPROVE: '승인', REJECT: '거절' }
const CHANGE_RESULTS = { SYNCED: '병합·동기화 완료', TIMEOUT: '기한 안에 병합되지 않음' }

export const toolLabel = (tool) => TOOL_LABELS[tool] || tool

export function actionDetail(args = {}) {
  const name = RUNBOOK_ACTIONS[args.action] || args.action
  const extra = [args.service, args.replicas != null ? `replicas=${args.replicas}` : null].filter(Boolean)
  return extra.length ? `${name} (${extra.join(', ')})` : name
}

function toolDetail(tool, args = {}) {
  if (tool === 'business_execute_runbook_action') return actionDetail(args)
  if (tool === 'rag_search_documents') return args.query
  if (tool === 'business_list_recent_changes') return args.service
  return args.expr || args.logql || args.metric || ''
}

/**
 * events: [{ type, data, createdAt }] (저장된 이력 + 실시간 이벤트를 시간순으로)
 * 행의 state: running(진행 중) | done | wait(사람·외부 대기) | ok | fail
 */
export function toTimeline(events) {
  const rows = []
  const settle = (match, state) => {
    for (const row of rows) if (match(row)) row.state = state
  }
  const add = (row, at) => {
    settle((r) => r.kind === 'step' && r.state === 'running', 'done')
    rows.push({ ...row, at })
  }

  for (const { type, data = {}, createdAt } of events) {
    switch (type) {
      case 'TOOL_START':
        add({ kind: 'tool', tool: data.tool, stage: STAGES[data.node], label: toolLabel(data.tool),
              detail: toolDetail(data.tool, data.args), state: 'running' }, createdAt)
        break
      case 'TOOL_END': {
        const row = rows.findLast((r) => r.kind === 'tool' && r.tool === data.tool && r.state === 'running')
        if (row) row.state = 'done'
        break
      }
      case 'STEP':
        add(data.node === 'verify'
          ? { kind: 'step', stage: STAGES.verify, label: `조치가 메트릭에 반영되기를 기다림 (${data.waitSeconds}초)`, state: 'running' }
          : { kind: 'step', stage: STAGES[data.node], label: '보고서 작성', state: 'running' }, createdAt)
        break
      case 'APPROVAL_REQUIRED':
        add({ kind: 'approval', stage: '승인', label: `승인 요청: ${toolLabel(data.tool)}`,
              detail: data.tool === 'business_execute_runbook_action' ? actionDetail(data.args) : data.reason,
              state: 'wait' }, createdAt)
        break
      case 'APPROVAL_DECIDED':
        settle((r) => r.kind === 'approval' && r.state === 'wait', 'done')
        add({ kind: 'decision', stage: '승인',
              label: data.by === 'auto-approve' ? '자동 승인 (테스트 설정)' : `${data.by}님이 ${DECISIONS[data.decision] || data.decision}`,
              detail: toolLabel(data.tool), state: data.decision === 'APPROVE' ? 'ok' : 'fail' }, createdAt)
        break
      case 'CHANGE_PENDING':
        add({ kind: 'change', stage: '반영', label: `PR #${data.prNumber} 병합·동기화 대기`, link: data.prUrl,
              state: 'wait' }, createdAt)
        break
      case 'CHANGE_FINISHED':
        settle((r) => r.kind === 'change' && r.state === 'wait', 'done')
        add({ kind: 'decision', stage: '반영', label: CHANGE_RESULTS[data.result] || data.result,
              state: data.result === 'SYNCED' ? 'ok' : 'fail' }, createdAt)
        break
      case 'DONE':
      case 'FAILED':
        settle((r) => r.state === 'running', 'done')
        add({ kind: 'end', stage: '종료', label: type === 'DONE' ? '작업 완료' : '작업 실패',
              state: type === 'DONE' ? 'ok' : 'fail' }, createdAt)
        break
    }
  }
  return rows
}

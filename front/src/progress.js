// 진행 이벤트를 사람이 읽는 타임라인으로 바꾼다 (기획안 5.3: "A사 에러율 조회 중…" 같은 상태 표시)

export const STAGES = {
  load_context: '컨텍스트 수집',
  diagnose: '진단',
  retrieve_runbook: '런북 검색',
  retrieve_history: '과거 장애 검색',
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
  rag_search_documents: '런북·계약·회고 검색',
}

export const RUNBOOK_ACTIONS = {
  restart_pods: '파드 재시작',
  scale_deployment: '레플리카 확장',
  rollback_deployment: '직전 배포로 롤백',
  adjust_autoscaler: 'HPA 최대치 올리기',
}

// backend EscalationService.CATEGORIES·BLOCKED_BY와 같다
export const ESCALATION_CATEGORIES = {
  P1: 'P1 장애 발생',
  NOT_RECOVERED: '조치 후에도 회복되지 않음',
  CHANGE_TIMEOUT: '조치 PR이 기한 안에 병합되지 않음',
  ACTION_BLOCKED: '필요한 조치를 계약·구조상 할 수 없음',
  DEPENDENCY: '원인이 다른 팀 소유의 의존 대상',
  CAUSE_UNKNOWN: '원인 미확정',
}
const BLOCKED_BY = { CONTRACT: '계약상 불가', STRUCTURE: '구조상 불가(상한·상태 저장)', RUNTIME: '지원하지 않는 실행 환경' }
const RECIPIENT_KINDS = { MSP_ONCALL: 'MSP 담당', CUSTOMER: '고객사 담당', DEPENDENCY_OWNER: '의존 대상 소유자' }

/** 에스컬레이션 받는 사람 한 줄 요약 (예: "MSP 담당 admin, eng-b · 고객사 담당 B사 운영 승인 담당") */
export function recipientsLabel(recipients = []) {
  const groups = new Map()
  for (const r of recipients) groups.set(r.kind, [...(groups.get(r.kind) || []), r.name])
  return [...groups].map(([kind, names]) => `${RECIPIENT_KINDS[kind] || kind} ${names.join(', ')}`).join(' · ')
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
        ['HPA 최대치', args.max_replicas != null ? `${args.max_replicas}개` : null],
        ['근거', args.reason],
        ['인시던트', args.incident_id != null ? `#${args.incident_id}` : null],
      ].filter(([, value]) => value)
    case 'business_send_customer_notice':
      return [['공지 내용', args.message], ['인시던트', args.incident_id != null ? `#${args.incident_id}` : '연결된 인시던트 없음']]
    case 'business_escalate_incident':
      return [
        ['분류', ESCALATION_CATEGORIES[args.category] || args.category],
        ['호출 사유', args.reason],
        ['대상 서비스', args.service],
        ['필요한 조치', RUNBOOK_ACTIONS[args.required_action] || args.required_action],
        ['할 수 없는 이유', BLOCKED_BY[args.blocked_by] || args.blocked_by],
        ['원인 의존 대상', args.dependency],
        ['인시던트', args.incident_id != null ? `#${args.incident_id}` : null],
      ].filter(([, value]) => value)
    default:
      return Object.entries(args).map(([key, value]) => [key, typeof value === 'object' ? JSON.stringify(value) : String(value)])
  }
}

/** 서비스 목록에서 계산된 현재 상태와 안전 제약. 모델이 만든 설명과 분리해 표시한다. */
export function approvalImpactFields({ tool, args = {}, impact = {} }) {
  if (tool !== 'business_execute_runbook_action') return []
  const rows = []
  if (impact.runtime) rows.push(['실행 환경', impact.runtime])
  if (impact.replicas != null) {
    const ready = impact.readyReplicas != null ? ` (준비됨 ${impact.readyReplicas}개)` : ''
    rows.push(['현재 레플리카', `${impact.replicas}개${ready}`])
  }
  if (args.action === 'restart_pods' && impact.restartMode) {
    rows.push(['재시작 방식', impact.restartMode === 'ROLLING'
      ? '순차 재시작 (전체 서비스 중단 없음)'
      : '전체 재시작 (서비스 중단 가능)'])
  }
  if (args.action === 'scale_deployment') {
    if (impact.targetReplicas != null) rows.push(['변경 후 레플리카', `${impact.targetReplicas}개`])
    if (impact.safeMaxReplicas != null) rows.push(['안전 상한', `${impact.safeMaxReplicas}개`])
  }
  if (args.action === 'adjust_autoscaler') {
    if (impact.autoscalerMaxReplicas != null) {
      rows.push(['HPA 범위', `${impact.autoscalerMinReplicas}~${impact.autoscalerMaxReplicas}개 → ${impact.autoscalerMinReplicas}~${impact.targetMaxReplicas}개`])
    }
    if (impact.safeMaxReplicas != null) rows.push(['안전 상한', `${impact.safeMaxReplicas}개`])
  }
  return rows
}

/** 승인하면 무엇이 바뀌는가 (기획안 5.3: 승인 카드의 '영향') */
export function approvalImpact({ tool, args = {}, changeMode, impact = {} }) {
  if (tool === 'business_send_customer_notice') return '고객사 담당자에게 위 내용으로 공지가 발송된다. 발송 후에는 취소할 수 없다.'
  if (tool === 'business_escalate_incident') {
    const who = { ACTION_BLOCKED: 'MSP 담당과 고객사 담당', DEPENDENCY: 'MSP 담당과 의존 대상 소유자' }[args.category] || 'MSP 담당'
    return `${who}에게 전달할 호출 기록을 남긴다. Slack 발송은 작업 상세에서 문안과 채널을 검토한 뒤 별도로 승인한다.`
  }
  if (tool !== 'business_execute_runbook_action') return null
  const service = args.service || '서비스'
  const what = {
    restart_pods: impact.restartMode === 'ROLLING'
      ? `${service}의 파드를 하나씩 재시작한다. 전체 서비스를 중단하지 않지만 재시작 중 처리량이 줄 수 있다.`
      : impact.restartMode === 'ALL_AT_ONCE'
        ? `${service}의 모든 파드를 함께 재시작하므로 재시작 중 서비스가 중단될 수 있다.`
        : `${service}의 파드를 재시작한다. 재시작하는 동안 처리량이 잠시 줄 수 있다.`,
    scale_deployment: `${service}의 레플리카 수를 ${args.replicas}개로 바꾼다.${impact.safeMaxReplicas != null ? ` 계산된 안전 상한은 ${impact.safeMaxReplicas}개다.` : ''}`,
    rollback_deployment: `${service}를 직전 배포(설정·이미지)로 되돌린다. 그 이후의 변경은 사라진다.`,
    adjust_autoscaler: `${service} HPA의 최대치를 ${args.max_replicas}개로 올린다. 레플리카는 HPA가 부하에 맞춰 그 안에서 정한다.`,
  }[args.action] || `${service}에 ${args.action} 조치를 실행한다.`
  const how = changeMode === 'GITOPS' && args.action !== 'restart_pods'
    ? ' 바로 반영되지 않고 설정 저장소에 PR이 만들어진다. PR이 병합되고 ArgoCD가 동기화하면 반영된다.'
    : ['DIRECT', 'GITOPS'].includes(changeMode) ? ' 승인 즉시 Kubernetes API로 반영된다.' : ''
  return what + how
}

export function approvalChangeMode({ args = {}, changeMode }) {
  return changeMode === 'GITOPS' && args.action !== 'restart_pods'
    ? '설정 저장소 PR (병합 후 ArgoCD 동기화)' : 'Kubernetes API로 바로 실행'
}

const DECISIONS = { APPROVE: '승인', REJECT: '거절' }
const CHANGE_RESULTS = { SYNCED: '병합·동기화 완료', TIMEOUT: '기한 안에 병합되지 않음' }

export const toolLabel = (tool) => TOOL_LABELS[tool] || tool

export function actionResultLabel(result) {
  return {
    EXECUTED: '조치 실행 완료', PR_CREATED: 'PR 생성 (반영 상태 별도 확인)',
    SENT: '공지 발송 완료', QUEUED: '호출 기록 등록 (Slack 발송 별도 검토)', FAILED: '실행 실패', UNCONFIRMED: '실행 결과 확인 불가', REJECTED: '발송 거절',
  }[result?.status] || '실행 확인 안 됨'
}

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
        add({ kind: 'tool', tool: data.tool, callId: data.callId,
              stage: STAGES[data.node], label: toolLabel(data.tool),
              detail: toolDetail(data.tool, data.args), state: 'running' }, createdAt)
        break
      case 'TOOL_END': {
        const row = rows.findLast((r) => r.kind === 'tool' && r.tool === data.tool && r.state === 'running'
          && (!data.callId || r.callId === data.callId))
        if (row) {
          row.state = data.status === 'error' ? 'fail' : 'done'
          if (data.error) row.detail = data.error
        }
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
        // 종료 이벤트만으로 아직 결과가 없는 도구를 성공 처리하지 않는다.
        settle((r) => r.kind === 'tool' && r.state === 'running', 'fail')
        settle((r) => r.kind === 'step' && r.state === 'running', type === 'DONE' ? 'done' : 'fail')
        add({ kind: 'end', stage: '종료', label: type === 'DONE' ? '작업 완료' : '작업 실패',
              state: type === 'DONE' ? 'ok' : 'fail' }, createdAt)
        break
    }
  }
  return rows
}

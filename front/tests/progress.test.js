import assert from 'node:assert/strict'
import test from 'node:test'
import { actionResultLabel, approvalChangeMode, approvalFields, approvalImpact, approvalImpactFields, recipientsLabel, toTimeline } from '../src/progress.js'

const event = (type, data = {}) => ({ type, data, createdAt: '2026-10-19T00:00:00Z' })
const start = (callId) => event('TOOL_START', { tool: 'business_execute_runbook_action', callId })

test('MCP error is shown as a failed action with the reason', () => {
  const rows = toTimeline([start('1'), event('TOOL_END', {
    tool: 'business_execute_runbook_action', callId: '1', status: 'error', error: '403: denied',
  }), event('DONE')])
  assert.equal(rows[0].state, 'fail')
  assert.equal(rows[0].detail, '403: denied')
})

test('an interrupted tool is not marked completed when the task fails', () => {
  assert.equal(toTimeline([start('1'), event('FAILED')])[0].state, 'fail')
})

test('call IDs associate overlapping uses of the same tool', () => {
  const rows = toTimeline([start('1'), start('2'), event('TOOL_END', {
    tool: 'business_execute_runbook_action', callId: '1', status: 'success',
  })])
  assert.deepEqual(rows.map(r => r.state), ['done', 'running'])
})

test('older successful events without call IDs are still readable', () => {
  const rows = toTimeline([start(undefined), event('TOOL_END', { tool: 'business_execute_runbook_action' })])
  assert.equal(rows[0].state, 'done')
})

test('Jev fault verdict is a finished row, not the report step', () => {
  const rows = toTimeline([event('STEP', { node: 'propose_action', fault: 'RB-STD-02', confidence: 1, applied: true }),
    event('STEP', { node: 'propose_action', fault: 'RB-STD-07', confidence: 0.99, applied: false })])
  assert.equal(rows[0].label, '장애 형태 판정 (Jev): RB-STD-02')
  assert.equal(rows[0].state, 'ok')
  assert.match(rows[0].detail, /100% · 조치 판단에 사용/)
  assert.match(rows[1].detail, /기록만/)
})

test('cause candidates chosen by the diagnosis are a finished row', () => {
  const [row] = toTimeline([event('STEP', { node: 'propose_action', causes: ['RB-STD-02', 'RB-STD-04'],
    target: 'RB-STD-02', facts: [] })])
  assert.equal(row.label, '처리할 원인: RB-STD-02')
  assert.equal(row.detail, '원인 후보 RB-STD-02 > RB-STD-04')
  assert.equal(row.state, 'ok')
})

test('an alert attached to the open incident is shown without a new task', () => {
  const [row] = toTimeline([event('ALERT_ADDED', { title: 'tenant-a order-api p95 응답시간 1초 초과', severity: 'P2' })])
  assert.equal(row.label, '알림 추가: tenant-a order-api p95 응답시간 1초 초과')
  assert.match(row.detail, /P2 · 같은 서비스/)
})

test('approval is not used as a substitute for execution outcome', () => {
  assert.equal(actionResultLabel(null), '실행 확인 안 됨')
  assert.equal(actionResultLabel({ status: 'FAILED' }), '실행 실패')
  assert.notEqual(actionResultLabel({ status: 'PR_CREATED' }), actionResultLabel({ status: 'EXECUTED' }))
})

test('reported symptom registration approval explains that it does not execute a recovery action', () => {
  const request = { tool: 'business_create_incident', args: {
    service: 'order-api', severity: 'P2', title: '제보 기반 점검: order-api',
  } }
  assert.deepEqual(Object.fromEntries(approvalFields(request)), {
    '대상 서비스': 'order-api', 심각도: 'P2', 제목: '제보 기반 점검: order-api',
  })
  assert.match(approvalImpact(request), /별도 승인 없이는 실행되지 않는다/)
})

test('GitOps pod restart is described as direct API execution, not a PR', () => {
  const request = { tool: 'business_execute_runbook_action', changeMode: 'GITOPS', args: { action: 'restart_pods' } }
  assert.equal(approvalChangeMode(request), 'Kubernetes API로 바로 실행')
  assert.doesNotMatch(approvalImpact(request), /PR/)
  request.args.action = 'scale_deployment'
  assert.match(approvalChangeMode(request), /PR/)
  assert.match(approvalImpact(request), /PR/)
})

test('approval shows rolling restart impact from the service inventory', () => {
  const request = { tool: 'business_execute_runbook_action', args: { action: 'restart_pods', service: 'order-api' },
    impact: { runtime: 'KUBERNETES', replicas: 2, readyReplicas: 2, restartMode: 'ROLLING', zeroDowntime: true } }
  const fields = Object.fromEntries(approvalImpactFields(request))
  assert.equal(fields['현재 레플리카'], '2개 (준비됨 2개)')
  assert.match(fields['재시작 방식'], /순차 재시작/)
  assert.match(approvalImpact(request), /전체 서비스를 중단하지 않지만/)
  request.impact.restartMode = 'ALL_AT_ONCE'
  assert.match(approvalImpact(request), /서비스가 중단될 수 있다/)
})

test('scale approval shows current, target, and calculated safe maximum', () => {
  const request = { tool: 'business_execute_runbook_action', args: { action: 'scale_deployment', service: 'order-api', replicas: 4 },
    impact: { replicas: 2, readyReplicas: 2, targetReplicas: 4, safeMaxReplicas: 4 } }
  const fields = Object.fromEntries(approvalImpactFields(request))
  assert.equal(fields['현재 레플리카'], '2개 (준비됨 2개)')
  assert.equal(fields['변경 후 레플리카'], '4개')
  assert.equal(fields['안전 상한'], '4개')
  assert.match(approvalImpact(request), /안전 상한은 4개/)
})

test('blocked-action escalation distinguishes a call record from reviewed Slack delivery', () => {
  const request = { tool: 'business_escalate_incident', args: {
    incident_id: 2, category: 'ACTION_BLOCKED', reason: '확장 계약상 불가', required_action: 'scale_deployment', blocked_by: 'CONTRACT' } }
  const fields = Object.fromEntries(approvalFields(request))
  assert.equal(fields['필요한 조치'], '레플리카 확장')
  assert.equal(fields['할 수 없는 이유'], '계약상 불가')
  assert.match(approvalImpact(request), /고객사 담당/)
  assert.match(approvalImpact(request), /Slack.*검토.*승인/)
  assert.equal(actionResultLabel({ status: 'QUEUED' }), '호출 기록 등록 (Slack 발송 별도 검토)')
  assert.equal(recipientsLabel([
    { kind: 'MSP_ONCALL', name: 'admin' }, { kind: 'MSP_ONCALL', name: 'eng-b' }, { kind: 'CUSTOMER', name: 'B사 담당' },
  ]), 'MSP 담당 admin, eng-b · 고객사 담당 B사 담당')
})

test('raising the autoscaler max shows the range change and the safe cap', () => {
  const request = { tool: 'business_execute_runbook_action', changeMode: 'GITOPS',
    args: { action: 'adjust_autoscaler', service: 'order-api', max_replicas: 4 },
    impact: { autoscalerMinReplicas: 2, autoscalerMaxReplicas: 3, targetMaxReplicas: 4, safeMaxReplicas: 4 } }
  const fields = Object.fromEntries(approvalImpactFields(request))
  assert.equal(fields['HPA 범위'], '2~3개 → 2~4개')
  assert.match(approvalImpact(request), /최대치를 4개로 올린다/)
  assert.match(approvalImpact(request), /PR/)
})

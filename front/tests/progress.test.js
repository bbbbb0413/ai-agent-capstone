import assert from 'node:assert/strict'
import test from 'node:test'
import { actionResultLabel, approvalChangeMode, approvalFields, approvalImpact, recipientsLabel, toTimeline } from '../src/progress.js'

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

test('approval is not used as a substitute for execution outcome', () => {
  assert.equal(actionResultLabel(null), '실행 확인 안 됨')
  assert.equal(actionResultLabel({ status: 'FAILED' }), '실행 실패')
  assert.notEqual(actionResultLabel({ status: 'PR_CREATED' }), actionResultLabel({ status: 'EXECUTED' }))
})

test('GitOps pod restart is described as direct API execution, not a PR', () => {
  const request = { tool: 'business_execute_runbook_action', changeMode: 'GITOPS', args: { action: 'restart_pods' } }
  assert.equal(approvalChangeMode(request), 'Kubernetes API로 바로 실행')
  assert.doesNotMatch(approvalImpact(request), /PR/)
  request.args.action = 'scale_deployment'
  assert.match(approvalChangeMode(request), /PR/)
  assert.match(approvalImpact(request), /PR/)
})

test('blocked-action escalation shows what is needed, why, and that delivery is not wired', () => {
  const request = { tool: 'business_escalate_incident', args: {
    incident_id: 2, category: 'ACTION_BLOCKED', reason: '확장 계약상 불가', required_action: 'scale_deployment', blocked_by: 'CONTRACT' } }
  const fields = Object.fromEntries(approvalFields(request))
  assert.equal(fields['필요한 조치'], '레플리카 확장')
  assert.equal(fields['할 수 없는 이유'], '계약상 불가')
  assert.match(approvalImpact(request), /고객사 담당/)
  assert.match(approvalImpact(request), /발송 대기열/)
  assert.equal(actionResultLabel({ status: 'QUEUED' }), '호출 대기열에 등록 (발송 미연결)')
  assert.equal(recipientsLabel([
    { kind: 'MSP_ONCALL', name: 'admin' }, { kind: 'MSP_ONCALL', name: 'eng-b' }, { kind: 'CUSTOMER', name: 'B사 담당' },
  ]), 'MSP 담당 admin, eng-b · 고객사 담당 B사 담당')
})

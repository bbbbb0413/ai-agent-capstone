import assert from 'node:assert/strict'
import test from 'node:test'
import { actionResultLabel, approvalChangeMode, approvalImpact, toTimeline } from '../src/progress.js'

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

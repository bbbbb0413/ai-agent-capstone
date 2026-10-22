import test from 'node:test'
import assert from 'node:assert/strict'
import { slackStatus, canReviewSlack } from '../src/slack.js'

test('sent and unconfirmed delivery cannot be sent again from the review UI', () => {
  for (const status of ['QUEUED', 'SENDING', 'SENT', 'UNKNOWN']) assert.equal(canReviewSlack(status), false)
  for (const status of [undefined, 'FAILED', 'CANCELLED']) assert.equal(canReviewSlack(status), true)
})

test('approved does not mean delivered', () => {
  assert.match(slackStatus('QUEUED'), /발송 대기/)
  assert.match(slackStatus('SENT'), /발송 완료/)
  assert.match(slackStatus('UNKNOWN'), /확인 필요/)
  assert.match(slackStatus(undefined), /검토 필요/)
})

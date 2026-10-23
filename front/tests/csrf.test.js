import test from 'node:test'
import assert from 'node:assert/strict'
import { csrfHeader } from '../src/api.js'

test('변경 요청은 XSRF-TOKEN 쿠키 값을 헤더로 보낸다', () => {
  const cookie = 'theme=dark; XSRF-TOKEN=abc%2D123; other=1'
  for (const method of ['POST', 'PUT', 'DELETE', 'PATCH']) {
    assert.deepEqual(csrfHeader(method, cookie), { 'X-XSRF-TOKEN': 'abc-123' })
  }
})

test('조회 요청과 토큰 쿠키가 없을 때는 헤더를 붙이지 않는다', () => {
  assert.deepEqual(csrfHeader('GET', 'XSRF-TOKEN=abc'), {})
  assert.deepEqual(csrfHeader('POST', 'theme=dark'), {})
  assert.deepEqual(csrfHeader('POST', ''), {})
})

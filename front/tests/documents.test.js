import test from 'node:test'
import assert from 'node:assert/strict'
import { COMMON, canEdit, hasPending, indexState, scopes, suggestDocId, typesFor } from '../src/documents.js'

// 문서 화면의 표시 규칙. 권한은 backend가 다시 보지만, 화면이 바꿀 수 없는 범위에 버튼을 보이면 안 된다

const admin = { role: 'MSP_ADMIN', tenants: [] }
const engineerA = { role: 'MSP_ENGINEER', tenants: ['tenant-a'] }
const viewerA = { role: 'TENANT_VIEWER', tenants: ['tenant-a'] }

test('공통 문서는 관리자만, 고객사 문서는 관리자와 담당 엔지니어만 바꾼다', () => {
  assert.equal(canEdit(admin, COMMON), true)
  assert.equal(canEdit(admin, 'tenant-b'), true)
  assert.equal(canEdit(engineerA, COMMON), false)
  assert.equal(canEdit(engineerA, 'tenant-a'), true)
  assert.equal(canEdit(engineerA, 'tenant-b'), false)
  assert.equal(canEdit(viewerA, 'tenant-a'), false)
  assert.equal(canEdit(null, 'tenant-a'), false)
})

test('범위는 공통이 먼저이고, 공통 문서의 종류는 표준 런북뿐이다', () => {
  assert.deepEqual(scopes([{ id: 'tenant-a', name: 'A사' }]).map((s) => s.id), [COMMON, 'tenant-a'])
  assert.deepEqual(typesFor(COMMON), ['runbook'])
  assert.deepEqual(typesFor('tenant-a'), ['runbook', 'sla'])
})

test('최신 버전이 실패하면 검색이 어느 버전으로 계속되는지 알린다', () => {
  const failed = { indexStatus: 'FAILED', indexError: '조항 번호가 바뀌었다', retryable: false }
  assert.equal(indexState(failed, 2).tone, 'bad')
  assert.match(indexState(failed, 2).note, /조항 번호가 바뀌었다 — 검색은 v2/)
  assert.match(indexState(failed, null).note, /검색에 나오지 않는다/)
  assert.match(indexState({ ...failed, retryable: true }, 1).label, /다시 시도 예정/)
  assert.equal(indexState({ indexStatus: 'INDEXED', chunkCount: 7 }, 3).note, '조각 7개')
  assert.equal(indexState({ indexStatus: 'PENDING' }, null).label, '색인 대기')
})

test('대기 중인 버전이 있을 때만 다시 읽는다', () => {
  assert.equal(hasPending([{ indexStatus: 'INDEXED' }, { indexStatus: 'PENDING' }]), true)
  assert.equal(hasPending([{ indexStatus: 'INDEXED' }, undefined]), false)
})

test('파일 이름에서 문서 ID 후보를 만든다', () => {
  assert.equal(suggestDocId('Runbook Order_API.docx'), 'runbook-order-api')
  assert.equal(suggestDocId('sla-contract.md'), 'sla-contract')
  assert.equal(suggestDocId('계약서.pdf'), '')   // 한글 이름은 사람이 적는다
})

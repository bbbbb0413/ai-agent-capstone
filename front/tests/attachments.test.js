import test from 'node:test'
import assert from 'node:assert/strict'
import { attachmentError } from '../src/attachments.js'
import { api } from '../src/api.js'
test('file selection validates type, count, per-file and total size', () => {
  assert.equal(attachmentError([{ name: 'error.log', size: 100 }, { name: 'screen.PNG', size: 100 }]), '')
  for (const files of [Array(4).fill({ name: 'a.txt', size: 1 }), [{ name: 'a.html', size: 1 }], [{ name: 'a.log', size: 0 }], [{ name: 'a.log', size: 524289 }], Array(3).fill({ name: 'a.png', size: 3*1024*1024 })]) assert.ok(attachmentError(files))
})
test('attachment API uses multipart without a JSON header or file data in the URL', async () => {
  const original = globalThis.fetch
  let request
  globalThis.fetch = async (url, init) => { request = { url, ...init }; return new Response(JSON.stringify({ taskId: 't' }), { status: 202 }) }
  try {
    const file = new File(['ERROR timeout'], 'error.log', { type: 'text/plain' })
    assert.deepEqual(await api.createAttachmentTask('tenant-a', '분석', [file]), { taskId: 't' })
    assert.equal(request.url, '/api/ai/tasks/with-attachments')
    assert.equal(request.headers['Content-Type'], undefined)
    assert.equal(request.body.get('tenantId'), 'tenant-a')
    assert.equal(await request.body.get('attachments').text(), 'ERROR timeout')
  } finally { globalThis.fetch = original }
})

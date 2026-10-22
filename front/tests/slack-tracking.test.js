import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile, unlink } from 'node:fs/promises'
import { parse, compileScript } from '@vue/compiler-sfc'
import { createRenderer, nextTick } from 'vue'

// 실제 Vue 컴포넌트를 DOM 대신 메모리 렌더러에서 실행한다. API 응답 순서와 이벤트 동작을 검사한다.
const source = await readFile(new URL('../src/components/SlackEscalations.vue', import.meta.url), 'utf8')
const { descriptor } = parse(source)
let code = compileScript(descriptor, { id: 'tracking-test', inlineTemplate: true,
  templateOptions: { compilerOptions: { hoistStatic: false } },
}).content
code = code.replace("import { api } from '../api.js'", 'export const api = {}')
  .replace("'../format.js'", "'../src/format.js'")
  .replace("'../slack.js'", "'../src/slack.js'")
  .replace("import { useSession } from '../session.js'", "const useSession = () => ({ user: { username: 'engineer' }, isAdmin: false })")
const file = new URL(`../.local/tracking-test-${process.pid}.mjs`, import.meta.url)
await mkdir(new URL('../.local/', import.meta.url), { recursive: true })
await writeFile(file, code)
const module = await import(file.href)
await unlink(file)

const node = (type, text = '') => ({ type, text, props: {}, children: [], parent: null,
  addEventListener() {}, removeEventListener() {},
  get options() { return this.children.filter(n => n.type === 'option') },
})
const renderer = createRenderer({
  createElement: (type) => node(type), createText: (text) => node('text', text), createComment: (text) => node('comment', text),
  setText: (n, text) => { n.text = text }, setElementText: (n, text) => { n.text = text; n.children = [] },
  patchProp: (n, key, prev, value) => { n.props[key] = value; if (key === 'value') n.value = value },
  insert(n, parent, anchor = null) {
    if (n.parent) n.parent.children = n.parent.children.filter(c => c !== n)
    n.parent = parent
    const i = anchor ? parent.children.indexOf(anchor) : -1
    if (i < 0) parent.children.push(n); else parent.children.splice(i, 0, n)
  },
  remove(n) { if (n.parent) n.parent.children = n.parent.children.filter(c => c !== n) },
  parentNode: n => n.parent, nextSibling: n => n.parent?.children[n.parent.children.indexOf(n) + 1] || null,
})
function all(n) { return [n, ...n.children.flatMap(all)] }
function text(n) { return n.text + n.children.map(text).join(' ') }
async function settle() { await new Promise(resolve => setImmediate(resolve)); await nextTick() }

function setup(status = 'WAITING', owner = null) {
  const events = []
  const handling = { deliveryId: 'delivery-1', status, assignedTo: owner, events }
  module.api.incidentEscalations = async () => [{ id: 1, reason: '담당자 호출' }]
  module.api.slackDeliveries = async () => [{ id: 'delivery-1', escalationId: 1, status: 'SENT', message: '테스트', channelLabel: '#test', approvedBy: 'admin' }]
  module.api.slackTracking = async () => [{ ...handling, events: [...events] }]
  const calls = []
  module.api.updateSlackTracking = async (incident, delivery, action) => {
    calls.push([incident, delivery, action])
    handling.status = { ACK: 'ACKNOWLEDGED', START: 'IN_PROGRESS', COMPLETE: 'COMPLETED' }[action]
    if (action === 'ACK') handling.acknowledgedBy = 'engineer'
    if (action === 'START') handling.assignedTo = 'engineer'
    if (action === 'COMPLETE') handling.completedBy = 'engineer'
    events.push({ action, actor: 'engineer', source: 'WEB', createdAt: '2026-10-22T00:00:00Z' })
  }
  const root = node('root')
  const app = renderer.createApp(module.default, { incidentId: 100 })
  app.mount(root)
  return { root, calls, close: () => app.unmount() }
}
function button(root, label) { return all(root).find(n => n.type === 'button' && text(n) === label) }

test('web tracking moves through confirmation, assignment and completion and displays history', async () => {
  const { root, calls, close } = setup()
  try {
    await settle()
    assert.match(text(root), /미확인/)
    await button(root, '확인했습니다').props.onClick()
    await settle()
    assert.match(text(root), /확인자 engineer/)
    assert.equal(button(root, '확인했습니다'), undefined)
    await button(root, '담당 시작').props.onClick()
    await settle()
    assert.match(text(root), /담당자 engineer/)
    await button(root, '처리 완료').props.onClick()
    await settle()
    assert.match(text(root), /완료자 engineer/)
    assert.equal(button(root, '처리 완료'), undefined)
    assert.equal(all(root).filter(n => n.type === 'ol')[0].children.filter(n => n.type === 'li').length, 3)
    assert.deepEqual(calls.map(c => c[2]), ['ACK', 'START', 'COMPLETE'])
    assert.ok(calls.every(c => c[0] === 100 && c[1] === 'delivery-1'))
  } finally { close() }
})

test('another engineer cannot see completion button for someone else’s assignment', async () => {
  const { root, close } = setup('IN_PROGRESS', 'other')
  try { await settle(); assert.equal(button(root, '처리 완료'), undefined); assert.match(text(root), /담당자 other/) }
  finally { close() }
})

test('rejected concurrent assignment keeps current state and displays server conflict', async () => {
  const { root, close } = setup()
  module.api.updateSlackTracking = async () => { throw new Error('이미 담당자가 처리 중입니다.') }
  try {
    await settle()
    await button(root, '담당 시작').props.onClick()
    await settle()
    assert.match(text(root), /이미 담당자가 처리 중입니다/)
    assert.match(text(root), /미확인/)
    assert.equal(button(root, '처리 완료'), undefined)
  } finally { close() }
})

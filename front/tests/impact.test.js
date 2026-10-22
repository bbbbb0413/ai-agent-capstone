import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile, unlink } from 'node:fs/promises'
import { parse, compileScript } from '@vue/compiler-sfc'
import { createRenderer, nextTick } from 'vue'

// 실제 Vue 컴포넌트를 DOM 대신 메모리 렌더러에서 실행한다. API 응답 순서와 이벤트 동작을 검사한다.
const source = await readFile(new URL('../src/components/ServiceImpact.vue', import.meta.url), 'utf8')
const { descriptor } = parse(source)
let code = compileScript(descriptor, { id: 'impact-test', inlineTemplate: true,
  templateOptions: { compilerOptions: { hoistStatic: false } },
}).content
code = code.replace("import { api } from '../api.js'", 'export const api = { impact: async () => { throw new Error("not configured") } }')
  .replace("'../format.js'", "'../src/format.js'")
const file = new URL(`../.local/impact-test-${process.pid}.mjs`, import.meta.url)
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
function fixture(tenantId, name = 'order-api') {
  return { tenantId, observedAt: '2026-10-22T00:00:00Z', window: '최근 1분', warnings: [],
    services: [{ name, status: 'ABNORMAL', errorRate: 0.1, p95Seconds: 2, requestsPerSecond: 10 }],
    dependencies: [{ id: 'database:db', name: 'db', kind: 'database', status: 'DEGRADED', consumers: [{ service: name, path: [name, 'db'], direct: true }] }],
    edges: [{ service: name, dependencyId: 'database:db', status: 'DEGRADED', p95Seconds: 1, callsPerSecond: 10 }] }
}
function mount() {
  const root = node('root')
  const app = renderer.createApp(module.default, { tenants: [{ id: 'tenant-a', name: 'A사' }, { id: 'tenant-b', name: 'B사' }] })
  app.mount(root)
  return { root, close: () => app.unmount() }
}

test('impact screen shows observations and service-node selection opens metric details', async () => {
  module.api.impact = async () => fixture('tenant-a')
  const { root, close } = mount()
  try {
    await settle()
    assert.match(text(root), /이상 관측 1개/)
    assert.match(text(root), /10.0%/)
    const button = all(root).find(n => n.props['aria-label'] === 'order-api: 이상 관측, 지표 보기')
    button.props.onClick()
    await nextTick()
    assert.match(text(root), /요청 10.00건\/초/)
  } finally { close() }
})

test('a slow response from a previous tenant cannot overwrite the newly selected tenant', async () => {
  let resolveA
  module.api.impact = tenant => tenant === 'tenant-a' ? new Promise(r => { resolveA = r }) : Promise.resolve(fixture(tenant, 'reservation-api'))
  const { root, close } = mount()
  try {
    await nextTick()
    all(root).find(n => n.type === 'select').props['onUpdate:modelValue']('tenant-b')
    await settle()
    assert.match(text(root), /reservation-api/)
    resolveA(fixture('tenant-a', 'private-a'))
    await settle()
    assert.doesNotMatch(text(root), /private-a/)
    assert.match(text(root), /reservation-api/)
  } finally { close() }
})

test('failed refresh removes previous observations and displays the failure', async () => {
  module.api.impact = async () => fixture('tenant-a')
  const { root, close } = mount()
  try {
    await settle()
    module.api.impact = async () => { throw new Error('조회 실패') }
    all(root).find(n => n.type === 'button' && text(n) === '새로고침').props.onClick()
    await settle()
    assert.match(text(root), /조회 실패/)
    assert.doesNotMatch(text(root), /order-api/)
  } finally { close() }
})

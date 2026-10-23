import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile, unlink } from 'node:fs/promises'
import { parse, compileScript } from '@vue/compiler-sfc'
import { createRenderer, nextTick } from 'vue'

// 실제 Vue 컴포넌트를 DOM 대신 메모리 렌더러에서 실행한다. 다시 실행 버튼의 노출 조건과 이동을 검사한다.
const source = await readFile(new URL('../src/components/TaskRetry.vue', import.meta.url), 'utf8')
const { descriptor } = parse(source)
let code = compileScript(descriptor, { id: 'task-retry-test', inlineTemplate: true,
  templateOptions: { compilerOptions: { hoistStatic: false } },
}).content
code = code.replace("import { api } from '../api.js'", 'export const api = {}')
  .replace("import { useRouter } from 'vue-router'", 'const useRouter = () => globalThis.__router')
const file = new URL(`../.local/task-retry-test-${process.pid}.mjs`, import.meta.url)
await mkdir(new URL('../.local/', import.meta.url), { recursive: true })
await writeFile(file, code)
const module = await import(file.href)
await unlink(file)

const node = (type, text = '') => ({ type, text, props: {}, children: [], parent: null })
const renderer = createRenderer({
  createElement: (type) => node(type), createText: (text) => node('text', text), createComment: (text) => node('comment', text),
  setText: (n, text) => { n.text = text }, setElementText: (n, text) => { n.text = text; n.children = [] },
  patchProp: (n, key, prev, value) => { n.props[key] = value },
  insert(n, parent, anchor = null) {
    if (n.parent) n.parent.children = n.parent.children.filter(c => c !== n)
    n.parent = parent
    const i = anchor ? parent.children.indexOf(anchor) : -1
    if (i < 0) parent.children.push(n); else parent.children.splice(i, 0, n)
  },
  remove(n) { if (n.parent) n.parent.children = n.parent.children.filter(c => c !== n) },
  parentNode: n => n.parent, nextSibling: n => n.parent?.children[n.parent.children.indexOf(n) + 1] || null,
})
const all = (n) => [n, ...n.children.flatMap(all)]
const text = (n) => n.text + n.children.map(text).join(' ')
const button = (root) => all(root).find(n => n.type === 'button')
async function settle() { await new Promise(resolve => setImmediate(resolve)); await nextTick() }

function mount(task, isAdmin) {
  const root = node('root')
  const app = renderer.createApp(module.default, { task, isAdmin })
  app.mount(root)
  return { root, close: () => app.unmount() }
}

const failed = { id: 'aaaaaaaa-1111', status: 'FAILED', retryOf: null, retriedBy: null }

test('관리자에게 실패한 작업의 다시 실행 버튼만 보인다', () => {
  for (const [task, isAdmin, shown] of [
    [failed, true, true],
    [failed, false, false],                              // 엔지니어
    [{ ...failed, status: 'DONE' }, true, false],        // 끝난 작업
    [{ ...failed, retriedBy: 'bbbbbbbb-2222' }, true, false],   // 이미 다시 실행함
  ]) {
    const { root, close } = mount(task, isAdmin)
    try { assert.equal(Boolean(button(root)), shown, JSON.stringify({ task, isAdmin })) } finally { close() }
  }
})

test('확인하면 다시 실행하고 새 작업 화면으로 간다', async () => {
  const pushed = []
  globalThis.__router = { push: (path) => pushed.push(path) }
  globalThis.window = { confirm: () => true }
  const calls = []
  module.api.retryTask = async (id) => { calls.push(id); return { taskId: 'cccccccc-3333' } }
  const { root, close } = mount(failed, true)
  try {
    await button(root).props.onClick()
    await settle()
    assert.deepEqual(calls, ['aaaaaaaa-1111'])
    assert.deepEqual(pushed, ['/tasks/cccccccc-3333'])
  } finally { close() }
})

test('취소하면 아무것도 하지 않고, 실패하면 이유를 보인다', async () => {
  globalThis.window = { confirm: () => false }
  let called = false
  module.api.retryTask = async () => { called = true; return {} }
  let mounted = mount(failed, true)
  try { await button(mounted.root).props.onClick(); assert.equal(called, false) } finally { mounted.close() }

  globalThis.window = { confirm: () => true }
  module.api.retryTask = async () => { throw new Error('이미 다시 실행한 작업이다') }
  mounted = mount(failed, true)
  try {
    await button(mounted.root).props.onClick(); await settle()
    assert.match(text(mounted.root), /이미 다시 실행한 작업이다/)
  } finally { mounted.close() }
})

test('원래 작업과 다시 실행한 작업을 서로 잇는다', () => {
  const { root, close } = mount({ ...failed, status: 'RUNNING', retryOf: 'dddddddd-4444' }, true)
  try { assert.match(text(root), /dddddddd/); assert.match(text(root), /다시 실행한 작업이다/) } finally { close() }
})

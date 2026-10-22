import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile, unlink } from 'node:fs/promises'
import { parse, compileScript } from '@vue/compiler-sfc'
import { createRenderer, nextTick } from 'vue'

// 실제 Vue 컴포넌트를 DOM 대신 메모리 렌더러에서 실행한다. API 응답 순서와 이벤트 동작을 검사한다.
const source = await readFile(new URL('../src/components/AskAgent.vue', import.meta.url), 'utf8')
const { descriptor } = parse(source)
let code = compileScript(descriptor, { id: 'ask-agent-test', inlineTemplate: true,
  templateOptions: { compilerOptions: { hoistStatic: false } },
}).content
code = code.replace("import { api } from '../api.js'", 'export const api = {}')
  .replace("'../attachments.js'", "'../src/attachments.js'")
const file = new URL(`../.local/ask-agent-test-${process.pid}.mjs`, import.meta.url)
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

function mount() {
  const root = node('root'); const created = []
  const app = renderer.createApp(module.default, { tenants: [{ id: 'tenant-a', name: 'A' }, { id: 'tenant-b', name: 'B' }], onCreated: id => created.push(id) })
  app.mount(root)
  return { root, created, close: () => app.unmount() }
}
function select(root, files) { all(root).find(n => n.type === 'input' && n.props.type === 'file').props.onChange({ target: { files } }) }
async function submit(root) { await all(root).find(n => n.type === 'form').props.onSubmit({ preventDefault() {} }); await settle() }

test('attachment-only form submits actual selected file and emits task ID', async () => {
  const calls = []
  module.api.createAttachmentTask = async (...args) => { calls.push(args); return { taskId: 'task-1' } }
  const { root, created, close } = mount()
  try {
    await settle()
    const file = new File(['ERROR timeout'], 'error.log')
    select(root, [file]); await settle()
    assert.match(text(root), /error.log/)
    assert.match(text(root), /첨부 자료 분석/)
    await submit(root)
    assert.deepEqual(calls[0], ['tenant-a', '', [file]])
    assert.deepEqual(created, ['task-1'])
    assert.doesNotMatch(text(root), /error.log/)
  } finally { close() }
})
test('changing tenant clears selected evidence to prevent accidental cross-tenant upload', async () => {
  const { root, close } = mount()
  try {
    await settle(); select(root, [new File(['ERROR'], 'private-a.log')]); await settle()
    all(root).find(n => n.type === 'select').props['onUpdate:modelValue']('tenant-b')
    await settle(); assert.doesNotMatch(text(root), /private-a.log/)
    assert.equal(all(root).find(n => n.type === 'button' && n.props.type === 'submit').props.disabled, true)
  } finally { close() }
})
test('upload failure preserves selected files for correction and displays the error', async () => {
  module.api.createAttachmentTask = async () => { throw new Error('업로드 실패') }
  const { root, close } = mount()
  try {
    await settle(); select(root, [new File(['ERROR'], 'error.log')]); await settle(); await submit(root)
    assert.match(text(root), /업로드 실패/); assert.match(text(root), /error.log/)
  } finally { close() }
})

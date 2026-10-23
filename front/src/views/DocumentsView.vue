<script setup>
// 근거 문서 관리 (SPEC-document-store 8장). agent가 검색하는 런북·SLA 계약서를 올리고 버전을 관리한다.
// 공통 문서는 MSP 관리자만, 고객사 문서는 관리자와 담당 엔지니어만 바꾼다. 권한은 backend가 다시 본다
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { api } from '../api.js'
import { ACCEPT, COMMON, TYPE_LABELS, canEdit, formatLabel, formatSize, hasPending, indexState, scopes,
  suggestDocId, typesFor } from '../documents.js'
import { shortDateTime } from '../format.js'
import { useSession } from '../session.js'

const POLL_MS = 3_000

const session = useSession()
const tenants = ref([])
const scope = ref(COMMON)
const rows = ref([])
const error = ref('')
const busy = ref(false)

const selected = ref(null)       // { document, versions, canEdit }
const preview = ref(null)        // { version, text }
const form = ref({ docType: 'runbook', docId: '', title: '', file: null })
const versionFile = ref(null)
let timer

const scopeList = computed(() => scopes(tenants.value))
const scopeName = (id) => scopeList.value.find((s) => s.id === id)?.name || id
const editable = computed(() => canEdit(session.user, scope.value))

async function load() {
  try {
    rows.value = await api.documents(scope.value)
    if (selected.value) selected.value = await api.document(selected.value.document.id)
    error.value = ''
  } catch (e) {
    error.value = e.message
  }
  schedule()
}

// 색인 대기 중인 버전이 있으면 상태가 바뀔 때까지 다시 읽는다
function schedule() {
  clearTimeout(timer)
  const waiting = hasPending(rows.value.map((r) => r.latest)) || hasPending(selected.value?.versions || [])
  if (waiting) timer = setTimeout(() => document.hidden || load(), POLL_MS)
}

async function open(id) {
  preview.value = null
  versionFile.value = null
  try {
    selected.value = await api.document(id)
    schedule()
  } catch (e) {
    error.value = e.message
  }
}

function pickFile(event) {
  const file = event.target.files?.[0] || null
  form.value.file = file
  if (file && !form.value.docId) form.value.docId = suggestDocId(file.name)
  if (file && !form.value.title) form.value.title = file.name.replace(/\.[^.]+$/, '')
}

async function run(action) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    await action()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

const create = () => run(async () => {
  const { documentId } = await api.createDocument({ tenant: scope.value, ...form.value })
  form.value = { docType: typesFor(scope.value)[0], docId: '', title: '', file: null }
  await load()
  await open(documentId)
})

const uploadVersion = () => run(async () => {
  await api.uploadDocumentVersion(selected.value.document.id, versionFile.value)
  versionFile.value = null
  await load()
})

const restore = (version) => run(async () => {
  if (!window.confirm(`v${version} 내용을 새 버전으로 다시 올린다. 이력은 그대로 남는다.`)) return
  await api.restoreDocumentVersion(selected.value.document.id, version)
  await load()
})

const remove = () => run(async () => {
  if (!window.confirm('문서를 지운다. agent 검색에서 빠지고, 저장된 버전 파일은 남는다.')) return
  await api.deleteDocument(selected.value.document.id)
  selected.value = null
  await load()
})

const showText = (version) => run(async () => {
  preview.value = { version, text: await api.documentText(selected.value.document.id, version) }
})

watch(scope, () => {
  selected.value = null
  preview.value = null
  form.value.docType = typesFor(scope.value)[0]
  load()
})

onMounted(async () => {
  tenants.value = await api.tenants().catch(() => [])
  await load()
})
onUnmounted(() => clearTimeout(timer))
</script>

<template>
  <p v-if="error" class="error">{{ error }}</p>

  <section class="card flush">
    <div class="card-head">
      <h2 class="card-title">근거 문서</h2>
      <div class="card-tools">
        <button v-for="s in scopeList" :key="s.id" :class="{ secondary: scope !== s.id }" @click="scope = s.id">
          {{ s.name }}
        </button>
      </div>
    </div>
    <p class="muted doc-note">
      agent가 장애를 진단할 때 검색하는 런북·SLA 계약서다. 올리면 몇 초 안에 색인되어 검색에 반영된다.
      <span v-if="!editable">{{ scope === COMMON ? '공통 문서는 MSP 관리자만 바꾼다.' : '담당 고객사의 문서만 바꾼다.' }}</span>
    </p>
    <p v-if="!rows.length" class="muted empty">{{ scopeName(scope) }} 문서가 없다</p>
    <table v-else class="grid-table">
      <thead>
        <tr><th>제목</th><th>문서 ID</th><th>종류</th><th>최신</th><th>검색</th><th>색인 상태</th><th>올린 사람</th><th></th></tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="row.document.id">
          <td>{{ row.document.title }}</td>
          <td class="nowrap"><code>{{ row.document.docId }}</code></td>
          <td>{{ TYPE_LABELS[row.document.docType] }}</td>
          <td class="nowrap">v{{ row.latest.version }} · {{ formatLabel(row.latest.format) }}</td>
          <td>{{ row.document.activeVersion ? `v${row.document.activeVersion}` : '-' }}</td>
          <td>
            <span class="badge" :class="indexState(row.latest, row.document.activeVersion).tone">
              {{ indexState(row.latest, row.document.activeVersion).label }}
            </span>
          </td>
          <td class="nowrap">{{ row.latest.uploadedBy }} · {{ shortDateTime(row.latest.uploadedAt) }}</td>
          <td><button class="link" @click="open(row.document.id)">열기 →</button></td>
        </tr>
      </tbody>
    </table>
  </section>

  <section v-if="editable" class="card">
    <h2>새 문서 ({{ scopeName(scope) }})</h2>
    <form class="doc-form" @submit.prevent="create">
      <label>파일 (md, txt, pdf, docx)
        <input type="file" :accept="ACCEPT" @change="pickFile" />
      </label>
      <label>종류
        <select v-model="form.docType">
          <option v-for="t in typesFor(scope)" :key="t" :value="t">{{ TYPE_LABELS[t] }}</option>
        </select>
      </label>
      <label>문서 ID (영문 소문자·숫자·-)
        <input v-model.trim="form.docId" placeholder="runbook-order-api" />
      </label>
      <label>제목
        <input v-model.trim="form.title" placeholder="order-api 서비스 런북" />
      </label>
      <div><button :disabled="busy || !form.file || !form.docId || !form.title">올리기</button></div>
    </form>
  </section>

  <section v-if="selected" class="card">
    <div class="row-between">
      <h2>{{ selected.document.title }} <span class="muted">({{ scopeName(selected.document.tenantId) }})</span></h2>
      <button class="link" @click="selected = null">닫기</button>
    </div>
    <p class="muted">
      문서 ID <code>{{ selected.document.docId }}</code> · {{ TYPE_LABELS[selected.document.docType] }} ·
      검색 버전 {{ selected.document.activeVersion ? `v${selected.document.activeVersion}` : '없음' }}
    </p>

    <div v-if="selected.canEdit" class="row doc-actions">
      <input type="file" :accept="ACCEPT" @change="versionFile = $event.target.files?.[0] || null" />
      <button :disabled="busy || !versionFile" @click="uploadVersion">새 버전 올리기</button>
      <button class="secondary" :disabled="busy" @click="remove">문서 삭제</button>
    </div>

    <table class="grid-table">
      <thead>
        <tr><th>버전</th><th>파일</th><th>색인 상태</th><th>올린 사람</th><th></th></tr>
      </thead>
      <tbody>
        <tr v-for="v in selected.versions" :key="v.id">
          <td class="nowrap">
            v{{ v.version }}
            <span v-if="v.version === selected.document.activeVersion" class="badge info">검색 중</span>
            <div v-if="v.restoredFrom" class="muted">v{{ v.restoredFrom }}에서 되돌림</div>
          </td>
          <td>{{ v.originalFilename }} <span class="muted">{{ formatLabel(v.format) }} · {{ formatSize(v.sizeBytes) }}</span></td>
          <td>
            <span class="badge" :class="indexState(v, selected.document.activeVersion).tone">
              {{ indexState(v, selected.document.activeVersion).label }}
            </span>
            <div v-if="indexState(v, selected.document.activeVersion).note" class="muted">
              {{ indexState(v, selected.document.activeVersion).note }}
            </div>
          </td>
          <td class="nowrap">{{ v.uploadedBy }} · {{ shortDateTime(v.uploadedAt) }}</td>
          <td class="nowrap">
            <a :href="api.documentOriginalUrl(selected.document.id, v.version)">원본 받기</a>
            <button class="link" :disabled="busy" @click="showText(v.version)">본문 보기</button>
            <button v-if="selected.canEdit && v.version !== selected.versions[0].version" class="link"
                    :disabled="busy" @click="restore(v.version)">이 버전으로 되돌리기</button>
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="preview" class="doc-preview">
      <h3>v{{ preview.version }} 본문 <span class="muted">(PDF·Word는 색인기가 뽑은 글자다)</span></h3>
      <pre>{{ preview.text }}</pre>
    </div>
  </section>
</template>

<style scoped>
.doc-note { margin: 0; padding: 10px 16px; border-bottom: 1px solid var(--border); font-size: 13px; }
.doc-form { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; align-items: end; }
.doc-actions { margin: 8px 0 12px; align-items: center; }
.doc-actions input { flex: 1; min-width: 200px; }
.doc-preview pre { max-height: 420px; overflow: auto; padding: 12px; background: var(--bg); border-radius: 4px; font-size: 13px; }
</style>

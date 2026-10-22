<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api.js'
import StatusBadge from '../components/StatusBadge.vue'
import TaskStats from '../components/TaskStats.vue'
import TrendChart from '../components/TrendChart.vue'
import SlackSettings from '../components/SlackSettings.vue'
import ServiceImpact from '../components/ServiceImpact.vue'
import { clockTime, shortDateTime } from '../format.js'
import { useSession } from '../session.js'
import { useSettings } from '../settings.js'

const REFRESH_MS = 10_000   // 알림으로 생긴 인시던트와 작업 상태 변화를 새로고침 없이 보여 준다
const TASK_TYPES = { DIAGNOSE: '장애 진단', QUESTION: '질문' }
const INCIDENT_FILTERS = [
  { key: 'open', label: '처리 중', match: (i) => i.status !== 'RESOLVED' },
  { key: 'resolved', label: '해결됨', match: (i) => i.status === 'RESOLVED' },
  { key: 'all', label: '전체', match: () => true },
]

const session = useSession()
const settings = useSettings()
const router = useRouter()
const route = useRoute()
const incidents = ref([])
const tasks = ref([])
const tenants = ref([])
const tenantId = ref('')
const question = ref('')
const error = ref('')
const updatedAt = ref(null)
const incidentFilter = ref('all')
let timer

const openCount = computed(() => incidents.value.filter((i) => i.status !== 'RESOLVED').length)
const activeTaskCount = computed(() => tasks.value.filter((t) => !['DONE', 'FAILED'].includes(t.status)).length)
const tabs = computed(() => [
  { key: 'dashboard', label: '대시보드' },
  { key: 'impact', label: '장애 영향 분석' },
  { key: 'tasks', label: `Agent 작업${activeTaskCount.value ? ` (${activeTaskCount.value})` : ''}` },
  ...(session.isAdmin ? [{ key: 'settings', label: '설정' }] : []),
])
const tab = computed(() => (tabs.value.some((t) => t.key === route.query.tab) ? route.query.tab : 'dashboard'))
const filteredIncidents = computed(() => {
  const filter = INCIDENT_FILTERS.find((f) => f.key === incidentFilter.value)
  return incidents.value.filter(filter.match)
})
const tenantName = (id) => tenants.value.find((t) => t.id === id)?.name || id

async function load() {
  try {
    ;[incidents.value, tasks.value] = await Promise.all([api.incidents(), api.tasks(), settings.load()])
    updatedAt.value = new Date()
    error.value = ''
  } catch (e) {
    error.value = e.message
  }
}

onMounted(async () => {
  try {
    tenants.value = await api.tenants()
    tenantId.value = tenants.value[0]?.id ?? ''
  } catch (e) {
    error.value = e.message
  }
  await load()
  timer = setInterval(() => document.hidden || load(), REFRESH_MS)
})
onUnmounted(() => clearInterval(timer))

function selectTab(key) {
  router.replace({ query: key === 'dashboard' ? {} : { tab: key } })
}

async function ask() {
  const { taskId } = await api.createTask(tenantId.value, question.value)
  router.push(`/tasks/${taskId}`)
}

async function resolveIncident(incident) {
  if (!confirm(`'${incident.title}' 인시던트를 해결됨으로 처리할까? 복구가 확인된 뒤에만 종료해야 한다.`)) return
  try {
    await api.resolveIncident(incident.id)
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function toggleAutoApprove(event) {
  const enabled = event.target.checked
  if (enabled && !confirm('자동 승인을 켜면 파드 재시작 같은 조치도 사람 확인 없이 실행된다. 켤까?')) {
    event.target.checked = false
    return
  }
  try {
    await settings.setAutoApprove(enabled)
  } catch (e) {
    event.target.checked = settings.autoApprove
    error.value = e.message
  }
}

function taskFor(incident) {
  return tasks.value.find((t) => t.incidentId === incident.id)
}
</script>

<template>
  <Teleport defer to="#page-tabs">
    <nav class="tabs">
      <button v-for="t in tabs" :key="t.key" class="tab" :class="{ active: t.key === tab }" @click="selectTab(t.key)">
        {{ t.label }}
      </button>
    </nav>
  </Teleport>

  <p v-if="error" class="error">{{ error }}</p>

  <template v-if="tab === 'dashboard'">
    <TaskStats :incidents="incidents" :tasks="tasks" />

    <form v-if="session.isMspStaff" class="card ask" @submit.prevent="ask">
      <h2>Agent에게 묻기</h2>
      <div class="row">
        <select v-model="tenantId">
          <option v-for="t in tenants" :key="t.id" :value="t.id">{{ t.name }} ({{ t.slaTier }})</option>
        </select>
        <input v-model="question" placeholder="예: A사 지금 왜 느려?" required />
        <button type="submit">질문</button>
      </div>
    </form>

    <section id="incidents" class="card flush">
      <div class="card-head">
        <h2 class="card-title">인시던트 <span v-if="openCount" class="badge bad">처리 중 {{ openCount }}</span></h2>
        <div class="card-tools">
          <span v-if="updatedAt" class="muted updated">{{ REFRESH_MS / 1000 }}초마다 갱신 · {{ clockTime(updatedAt) }}</span>
          <div class="segmented">
            <button v-for="f in INCIDENT_FILTERS" :key="f.key" :class="{ active: f.key === incidentFilter }"
                    @click="incidentFilter = f.key">{{ f.label }}</button>
          </div>
        </div>
      </div>
      <table class="grid-table">
        <thead>
          <tr><th>시작</th><th>고객사</th><th>서비스</th><th>제목</th><th>심각도</th><th>상태</th><th>Agent 작업</th><th v-if="session.isAdmin">관리</th></tr>
        </thead>
        <tbody>
          <tr v-for="i in filteredIncidents" :key="i.id">
            <td class="nowrap">{{ shortDateTime(i.startedAt) }}</td>
            <td>{{ tenantName(i.tenantId) }}</td>
            <td>{{ i.service }}</td>
            <td>{{ i.title }}</td>
            <td><StatusBadge kind="severity" :value="i.severity" /></td>
            <td><StatusBadge kind="incident" :value="i.status" /></td>
            <td>
              <RouterLink v-if="taskFor(i)" :to="`/tasks/${taskFor(i).id}`" class="badge-link">
                <StatusBadge kind="task" :value="taskFor(i).status" /> 보기 →
              </RouterLink>
            </td>
            <td v-if="session.isAdmin">
              <button v-if="i.status !== 'RESOLVED'" class="secondary" @click="resolveIncident(i)">해결 처리</button>
            </td>
          </tr>
          <tr v-if="!filteredIncidents.length"><td :colspan="session.isAdmin ? 8 : 7" class="muted empty">해당하는 인시던트가 없다</td></tr>
        </tbody>
      </table>
    </section>

    <TrendChart :incidents="incidents" :tasks="tasks" :updated-at="updatedAt" @refresh="load" />
  </template>

  <ServiceImpact v-else-if="tab === 'impact'" :tenants="tenants" />

  <section v-else-if="tab === 'tasks'" class="card flush">
    <table class="grid-table">
      <thead>
        <tr><th>시작</th><th>고객사</th><th>종류</th><th>내용</th><th>상태</th><th></th></tr>
      </thead>
      <tbody>
        <tr v-for="t in tasks" :key="t.id">
          <td class="nowrap">{{ shortDateTime(t.createdAt) }}</td>
          <td>{{ tenantName(t.tenantId) }}</td>
          <td class="nowrap">{{ TASK_TYPES[t.type] || t.type }}</td>
          <td class="ellipsis" :title="t.question">{{ t.question }}</td>
          <td><StatusBadge kind="task" :value="t.status" /></td>
          <td><RouterLink :to="`/tasks/${t.id}`" class="badge-link">보기 →</RouterLink></td>
        </tr>
        <tr v-if="!tasks.length"><td colspan="6" class="muted empty">아직 작업이 없다</td></tr>
      </tbody>
    </table>
  </section>

  <template v-else-if="tab === 'settings'">
  <SlackSettings :tenants="tenants" />
  <section v-if="settings.autoApproveAllowed" class="card">
    <h2>테스트 설정</h2>
    <label class="toggle">
      <input type="checkbox" :checked="settings.autoApprove" @change="toggleAutoApprove" />
      자동 승인 (테스트용)
    </label>
    <p class="muted">
      켜면 조치·담당자 호출·고객 공지 승인 요청을 사람 확인 없이 바로 승인한다.
      켜기 전부터 승인 대기 중인 작업에는 적용되지 않는다. Slack 외부 발송은 항상 별도로 검토·승인한다.
    </p>
  </section>
  </template>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api.js'
import StatusBadge from '../components/StatusBadge.vue'
import TaskStats from '../components/TaskStats.vue'
import { useSession } from '../session.js'

const REFRESH_MS = 10_000   // 알림으로 생긴 인시던트와 작업 상태 변화를 새로고침 없이 보여 준다

const session = useSession()
const router = useRouter()
const incidents = ref([])
const tasks = ref([])
const tenants = ref([])
const tenantId = ref('')
const question = ref('')
const error = ref('')
const updatedAt = ref(null)
let timer

async function load() {
  try {
    ;[incidents.value, tasks.value] = await Promise.all([api.incidents(), api.tasks()])
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

async function ask() {
  const { taskId } = await api.createTask(tenantId.value, question.value)
  router.push(`/tasks/${taskId}`)
}

function taskFor(incident) {
  return tasks.value.find((t) => t.incidentId === incident.id)
}
</script>

<template>
  <p v-if="error" class="error">{{ error }}</p>

  <TaskStats :incidents="incidents" :tasks="tasks" />

  <form v-if="session.isMspStaff" class="card" @submit.prevent="ask">
    <h2>Agent에게 묻기</h2>
    <div class="row">
      <select v-model="tenantId">
        <option v-for="t in tenants" :key="t.id" :value="t.id">{{ t.name }} ({{ t.slaTier }})</option>
      </select>
      <input v-model="question" placeholder="예: A사 지금 왜 느려?" required />
      <button type="submit">질문</button>
    </div>
  </form>

  <section class="card">
    <h2 class="row-between">
      인시던트
      <span v-if="updatedAt" class="muted updated">{{ REFRESH_MS / 1000 }}초마다 갱신 · {{ updatedAt.toLocaleTimeString() }}</span>
    </h2>
    <table>
      <thead>
        <tr><th>시작</th><th>고객사</th><th>서비스</th><th>제목</th><th>심각도</th><th>상태</th><th>Agent 작업</th></tr>
      </thead>
      <tbody>
        <tr v-for="i in incidents" :key="i.id">
          <td>{{ new Date(i.startedAt).toLocaleString() }}</td>
          <td>{{ i.tenantId }}</td>
          <td>{{ i.service }}</td>
          <td>{{ i.title }}</td>
          <td><StatusBadge kind="severity" :value="i.severity" /></td>
          <td><StatusBadge kind="incident" :value="i.status" /></td>
          <td>
            <RouterLink v-if="taskFor(i)" :to="`/tasks/${taskFor(i).id}`" class="badge-link">
              <StatusBadge kind="task" :value="taskFor(i).status" /> 보기 →
            </RouterLink>
          </td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

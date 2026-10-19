<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api.js'
import { useSession } from '../session.js'

const session = useSession()
const router = useRouter()
const incidents = ref([])
const tasks = ref([])
const tenants = ref([])
const tenantId = ref('')
const question = ref('')
const error = ref('')

onMounted(async () => {
  try {
    ;[incidents.value, tasks.value, tenants.value] = await Promise.all([api.incidents(), api.tasks(), api.tenants()])
    tenantId.value = tenants.value[0]?.id ?? ''
  } catch (e) {
    error.value = e.message
  }
})

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
    <h2>인시던트</h2>
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
          <td>{{ i.severity }}</td>
          <td>{{ i.status }}</td>
          <td>
            <RouterLink v-if="taskFor(i)" :to="`/tasks/${taskFor(i).id}`">{{ taskFor(i).status }}</RouterLink>
          </td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

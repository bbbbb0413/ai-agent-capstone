<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api.js'
import { useSession } from '../session.js'

const props = defineProps({ id: { type: String, required: true } })
const session = useSession()
const task = ref(null)
const answer = ref('')
const activity = ref([])
const approval = ref(null)
const error = ref('')
let source

// 진행 이벤트: 화면 영역마다 필요한 이벤트만 받는다 (기획안 5.3)
function subscribe() {
  source = api.stream(props.id)
  source.addEventListener('TOKEN', (e) => (answer.value += JSON.parse(e.data).text))
  source.addEventListener('TOOL_START', (e) => activity.value.push(`${JSON.parse(e.data).tool} 실행 중…`))
  source.addEventListener('TOOL_END', (e) => activity.value.push(`${JSON.parse(e.data).tool} 완료`))
  source.addEventListener('APPROVAL_REQUIRED', (e) => (approval.value = JSON.parse(e.data)))
  for (const type of ['DONE', 'FAILED']) {
    source.addEventListener(type, async () => {
      source.close()
      task.value = await api.task(props.id)
    })
  }
}

onMounted(async () => {
  try {
    task.value = await api.task(props.id)
    if (task.value.status === 'WAITING_APPROVAL') approval.value = task.value.pendingAction
    if (!['DONE', 'FAILED'].includes(task.value.status)) subscribe()
  } catch (e) {
    error.value = e.message
  }
})
onUnmounted(() => source?.close())

async function decide(decision) {
  try {
    await api.approve(props.id, decision)
    approval.value = null
    if (!source || source.readyState === EventSource.CLOSED) subscribe()
  } catch (e) {
    error.value = e.message
  }
}
</script>

<template>
  <p v-if="error" class="error">{{ error }}</p>
  <section v-if="task" class="card">
    <h2>작업 {{ task.type }} · {{ task.tenantId }} · {{ task.status }}</h2>
    <p class="muted">{{ task.question }}</p>
  </section>

  <section v-if="approval" class="card approval">
    <h2>승인 필요: {{ approval.tool }}</h2>
    <p>{{ approval.reason }}</p>
    <pre>{{ JSON.stringify(approval.args, null, 2) }}</pre>
    <details v-if="approval.runbook?.length">
      <summary>근거 문서</summary>
      <ul><li v-for="d in approval.runbook" :key="d.uri + d.section">{{ d.title }} · {{ d.section }}</li></ul>
    </details>
    <div v-if="session.isMspStaff" class="row">
      <button @click="decide('APPROVE')">승인</button>
      <button class="secondary" @click="decide('REJECT')">거절</button>
    </div>
  </section>

  <section class="card">
    <h2>진행 상황</h2>
    <ul class="activity"><li v-for="(line, i) in activity" :key="i">{{ line }}</li></ul>
  </section>

  <section class="card">
    <h2>리포트</h2>
    <pre class="report">{{ task?.answer || answer }}</pre>
    <ul v-if="task?.sources?.length" class="muted">
      <li v-for="s in task.sources" :key="s.doc + s.section">근거: {{ s.doc }} · {{ s.section }}</li>
    </ul>
    <a v-if="task?.traceUrl" :href="task.traceUrl" target="_blank" rel="noopener">trace 보기</a>
  </section>
</template>

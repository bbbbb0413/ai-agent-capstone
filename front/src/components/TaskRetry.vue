<script setup>
// 실패한 작업 다시 실행 (MSP 관리자). 같은 내용으로 새 작업을 만들고 그 화면으로 간다. 원래 작업은 실패로 남는다
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api.js'

const props = defineProps({
  task: { type: Object, required: true },
  isAdmin: { type: Boolean, default: false },
})
const router = useRouter()
const busy = ref(false)
const error = ref('')
const canRetry = computed(() => props.isAdmin && props.task.status === 'FAILED' && !props.task.retriedBy)
const short = (id) => String(id).slice(0, 8)

async function retry() {
  if (busy.value || !window.confirm('같은 내용으로 새 작업을 만든다. 원래 작업은 실패로 남는다.')) return
  busy.value = true
  error.value = ''
  try {
    const { taskId } = await api.retryTask(props.task.id)
    router.push(`/tasks/${taskId}`)
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <p v-if="task.retryOf" class="muted">
    실패한 작업 <RouterLink :to="`/tasks/${task.retryOf}`">{{ short(task.retryOf) }}</RouterLink>을 다시 실행한 작업이다
  </p>
  <p v-if="task.retriedBy" class="muted">
    다시 실행한 작업: <RouterLink :to="`/tasks/${task.retriedBy}`">{{ short(task.retriedBy) }}</RouterLink>
  </p>
  <p v-if="canRetry">
    <button :disabled="busy" @click="retry">{{ busy ? '만드는 중…' : '다시 실행' }}</button>
  </p>
  <p v-if="error" class="error">{{ error }}</p>
</template>

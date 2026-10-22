<script setup>
import { ref, watch, onUnmounted } from 'vue'
import { api } from '../api.js'
import { ATTACHMENT_ACCEPT, attachmentError, fileSize } from '../attachments.js'
const props = defineProps({ tenants: { type: Array, required: true } })
const emit = defineEmits(['created'])
const tenant = ref('')
const question = ref('')
const files = ref([])
const input = ref(null)
const busy = ref(false)
const error = ref('')
watch(() => props.tenants, ts => { if (!ts.some(t => t.id === tenant.value)) tenant.value = ts[0]?.id || '' }, { immediate: true })
function clear() { files.value.forEach(f => f.preview && URL.revokeObjectURL(f.preview)); files.value = []; if (input.value) input.value.value = '' }
watch(tenant, () => { clear(); error.value = '' })
onUnmounted(clear)
function select(event) {
  const selected = Array.from(event.target.files || [])
  error.value = attachmentError(selected)
  clear()
  if (error.value) return
  files.value = selected.map(file => ({ file, preview: /\.(png|jpe?g)$/i.test(file.name) ? URL.createObjectURL(file) : null }))
}
function remove(index) {
  const [removed] = files.value.splice(index, 1)
  if (removed.preview) URL.revokeObjectURL(removed.preview)
  if (input.value) input.value.value = ''
}
async function ask() {
  if (busy.value || !tenant.value || (!question.value.trim() && !files.value.length)) return
  busy.value = true; error.value = ''
  try {
    const result = files.value.length
      ? await api.createAttachmentTask(tenant.value, question.value, files.value.map(f => f.file))
      : await api.createTask(tenant.value, question.value)
    clear(); emit('created', result.taskId)
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}
</script>
<template>
  <form class="card ask" @submit.prevent="ask">
    <h2>Agent에게 묻기</h2>
    <div class="row">
      <label>고객사<select v-model="tenant" :disabled="busy"><option v-for="t in tenants" :key="t.id" :value="t.id">{{ t.name }} ({{ t.slaTier }})</option></select></label>
      <label class="question">질문<input v-model="question" maxlength="4000" :disabled="busy" :required="!files.length" placeholder="예: 이 에러 화면과 로그의 원인을 분석해줘" /></label>
      <button type="submit" :disabled="busy || !tenant || (!question.trim() && !files.length)">{{ busy ? '전달 중…' : files.length ? '첨부 자료 분석' : '질문' }}</button>
    </div>
    <label class="attach">로그·에러 화면 첨부<input ref="input" type="file" multiple :accept="ATTACHMENT_ACCEPT" :disabled="busy" @change="select" /></label>
    <p class="muted">최대 3개 · PNG/JPEG 각 4MB, UTF-8 로그 각 512KB · 합계 8MB. 첨부 자료는 AI 분석에 사용됩니다.</p>
    <ul v-if="files.length" class="selected-files">
      <li v-for="(item,i) in files" :key="i">
        <img v-if="item.preview" :src="item.preview" :alt="`${item.file.name} 미리보기`" />
        <span>{{ item.file.name }} <small>{{ fileSize(item.file.size) }}</small></span>
        <button type="button" class="secondary" :disabled="busy" :aria-label="`${item.file.name} 첨부 삭제`" @click="remove(i)">삭제</button>
      </li>
    </ul>
    <p v-if="files.length" class="muted">이미지 속 비밀번호·키는 가린 뒤 첨부하세요. 분석 결과는 원인 후보와 확인 방법으로 제공됩니다.</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
  </form>
</template>
<style scoped>
.row { align-items: end; }.row label { display: grid; gap: 6px; }.question { flex: 1; }.attach { display: grid; gap: 8px; margin-top: 16px; }.selected-files { list-style: none; padding: 0; }.selected-files li { display: flex; gap: 12px; align-items: center; padding: 8px 0; }.selected-files span { flex: 1; overflow-wrap: anywhere; }.selected-files img { max-width: 100px; max-height: 70px; border-radius: 6px; }.selected-files small { color: var(--muted); }
</style>

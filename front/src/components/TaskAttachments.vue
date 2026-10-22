<script setup>
import { ref, watch, onUnmounted } from 'vue'
import { api } from '../api.js'
import { fileSize } from '../attachments.js'
const props = defineProps({ taskId: { type: String, required: true } })
const files = ref([])
const error = ref('')
let generation = 0
watch(() => props.taskId, async id => {
  const current = ++generation
  files.value = []; error.value = ''
  try { const result = await api.taskAttachments(id); if (current === generation) files.value = result }
  catch (e) { if (current === generation) error.value = e.message }
}, { immediate: true })
onUnmounted(() => ++generation)
</script>
<template>
  <section v-if="files.length || error" class="card">
    <h2>분석에 사용한 첨부 자료</h2>
    <p v-if="error" class="error">{{ error }}</p>
    <ul><li v-for="f in files" :key="f.id"><a :href="`/api/ai/tasks/${taskId}/attachments/${f.id}`" download>{{ f.filename }}</a> · {{ fileSize(f.size) }}</li></ul>
    <p class="muted">사용자가 제공한 시점의 자료입니다. 현재 모니터링 상태와 다를 수 있습니다.</p>
  </section>
</template>

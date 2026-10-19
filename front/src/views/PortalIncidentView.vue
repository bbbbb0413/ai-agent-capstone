<script setup>
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import { computed, onMounted, ref } from 'vue'
import { api } from '../api.js'
import StatusBadge from '../components/StatusBadge.vue'

const props = defineProps({ id: { type: String, required: true } })
const incident = ref(null)
const notices = ref([])
const error = ref('')

// 보고서(prompts/report.md) 중 고객이 볼 절만 남긴다. 조사 과정·근거(도구, 쿼리)·권장 다음 행동은 MSP 내부용이다
const CUSTOMER_SECTIONS = ['요약', '조치와 결과', 'SLA 영향']

function customerReport(markdown) {
  const sections = markdown.split(/^(?=## )/m)
  return sections.filter((s) => CUSTOMER_SECTIONS.some((title) => s.startsWith(`## ${title}`))).join('\n')
}

const reportHtml = computed(() => {
  const text = incident.value?.report ? customerReport(incident.value.report) : ''
  return text ? DOMPurify.sanitize(marked.parse(text)) : ''
})

const duration = computed(() => {
  if (!incident.value) return ''
  const end = incident.value.resolvedAt ? new Date(incident.value.resolvedAt) : new Date()
  const minutes = Math.max(0, Math.round((end - new Date(incident.value.startedAt)) / 60000))
  return incident.value.resolvedAt ? `${minutes}분 만에 해결` : `${minutes}분째 처리 중`
})

onMounted(async () => {
  try {
    ;[incident.value, notices.value] = await Promise.all([api.incident(props.id), api.incidentNotices(props.id)])
  } catch (e) {
    error.value = e.message
  }
})
</script>

<template>
  <RouterLink to="/portal" class="back">← 인시던트 목록</RouterLink>
  <p v-if="error" class="error">{{ error }}</p>

  <template v-if="incident">
    <section class="card">
      <h2>
        {{ incident.title }}
        <StatusBadge kind="incident" :value="incident.status" />
        <StatusBadge kind="severity" :value="incident.severity" />
      </h2>
      <table class="summary">
        <tbody>
          <tr><th>서비스</th><td>{{ incident.service }}</td></tr>
          <tr><th>발생</th><td>{{ new Date(incident.startedAt).toLocaleString() }}</td></tr>
          <tr v-if="incident.resolvedAt"><th>해결</th><td>{{ new Date(incident.resolvedAt).toLocaleString() }}</td></tr>
          <tr><th>처리 시간</th><td>{{ duration }}</td></tr>
        </tbody>
      </table>
    </section>

    <section class="card">
      <h2>안내 공지</h2>
      <ul v-if="notices.length" class="notices">
        <li v-for="(n, i) in notices" :key="i">
          <span class="muted">{{ new Date(n.sentAt).toLocaleString() }}</span>
          <p class="pre-line">{{ n.message }}</p>
        </li>
      </ul>
      <p v-else class="muted">발송된 공지가 없다</p>
    </section>

    <section class="card">
      <h2>장애 보고서</h2>
      <div v-if="reportHtml" class="markdown" v-html="reportHtml"></div>
      <p v-else class="muted">보고서를 작성하고 있다. 처리가 끝나면 이곳에 표시된다.</p>
    </section>
  </template>
</template>

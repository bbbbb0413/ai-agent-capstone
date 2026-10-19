<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api.js'
import StatusBadge from '../components/StatusBadge.vue'

const REFRESH_MS = 30_000

const incidents = ref([])
const tenants = ref([])
const error = ref('')
let timer

const tenantName = (id) => tenants.value.find((t) => t.id === id)?.name || id
const openCount = computed(() => incidents.value.filter((i) => i.status !== 'RESOLVED').length)
const resolvedCount = computed(() => incidents.value.filter((i) => i.status === 'RESOLVED').length)

async function load() {
  try {
    incidents.value = await api.incidents()
    error.value = ''
  } catch (e) {
    error.value = e.message
  }
}

onMounted(async () => {
  tenants.value = await api.tenants().catch(() => [])
  await load()
  timer = setInterval(() => document.hidden || load(), REFRESH_MS)
})
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <p v-if="error" class="error">{{ error }}</p>

  <section class="card">
    <h2>{{ tenants.length === 1 ? tenants[0].name : '고객사' }} 장애 현황</h2>
    <p v-if="tenants.length === 1" class="muted">
      SLA 등급 {{ tenants[0].slaTier }} · 가용성 목표 {{ tenants[0].availabilityTarget }}% · 응답 기한 {{ tenants[0].responseMinutes }}분
    </p>
    <div class="kpis portal-kpis">
      <div class="kpi"><span class="muted">처리 중</span><strong class="error">{{ openCount }}</strong></div>
      <div class="kpi"><span class="muted">해결됨</span><strong class="ok">{{ resolvedCount }}</strong></div>
    </div>
  </section>

  <section class="card">
    <h2>인시던트</h2>
    <p v-if="!incidents.length" class="muted">등록된 인시던트가 없다</p>
    <table v-else>
      <thead>
        <tr><th>발생</th><th v-if="tenants.length > 1">고객사</th><th>서비스</th><th>내용</th><th>심각도</th><th>상태</th><th></th></tr>
      </thead>
      <tbody>
        <tr v-for="i in incidents" :key="i.id">
          <td>{{ new Date(i.startedAt).toLocaleString() }}</td>
          <td v-if="tenants.length > 1">{{ tenantName(i.tenantId) }}</td>
          <td>{{ i.service }}</td>
          <td>{{ i.title }}</td>
          <td><StatusBadge kind="severity" :value="i.severity" /></td>
          <td><StatusBadge kind="incident" :value="i.status" /></td>
          <td><RouterLink :to="`/portal/incidents/${i.id}`">{{ i.report ? '보고서 보기 →' : '상세 →' }}</RouterLink></td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

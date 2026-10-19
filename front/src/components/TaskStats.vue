<script setup>
import { computed } from 'vue'

const props = defineProps({
  incidents: { type: Array, required: true },
  tasks: { type: Array, required: true },
})

const OUTCOMES = [
  { key: 'DONE', label: '성공', color: 'var(--ok)' },
  { key: 'FAILED', label: '실패', color: 'var(--danger)' },
  { key: 'WAITING', label: '승인·반영 대기', color: 'var(--warn)', statuses: ['WAITING_APPROVAL', 'CHANGE_PENDING'] },
  { key: 'RUNNING', label: '진행 중', color: 'var(--accent)', statuses: ['RUNNING', 'QUEUED'] },
]

const outcomes = computed(() =>
  OUTCOMES.map((o) => ({
    ...o,
    count: props.tasks.filter((t) => (o.statuses || [o.key]).includes(t.status)).length,
  })),
)
const count = (key) => outcomes.value.find((o) => o.key === key).count

// 도넛 조각: 둘레가 100인 원(r=15.915)에 stroke-dasharray로 비율만큼 그린다
const slices = computed(() => {
  const total = props.tasks.length
  let offset = 0
  return outcomes.value
    .filter((o) => o.count)
    .map((o) => {
      const length = (o.count / total) * 100
      const slice = { ...o, dash: `${length} ${100 - length}`, offset: 25 - offset }
      offset += length
      return slice
    })
})

// 에러 유형은 Agent가 정형 요약을 남긴 작업만 센다 (실패한 작업에는 요약이 없다)
const errorTypes = computed(() => {
  const counts = {}
  for (const t of props.tasks) {
    const type = t.summary?.error_type
    if (type) counts[type] = (counts[type] || 0) + 1
  }
  return Object.entries(counts).sort((a, b) => b[1] - a[1])
})
const maxErrorType = computed(() => Math.max(1, ...errorTypes.value.map(([, n]) => n)))
</script>

<template>
  <section class="card">
    <h2>현황</h2>
    <div class="kpis">
      <div class="kpi"><span class="muted">장애</span><strong>{{ incidents.length }}</strong></div>
      <div class="kpi"><span class="muted">성공</span><strong class="ok">{{ count('DONE') }}</strong></div>
      <div class="kpi"><span class="muted">실패</span><strong class="error">{{ count('FAILED') }}</strong></div>
      <div class="kpi"><span class="muted">처리 중</span><strong>{{ count('WAITING') + count('RUNNING') }}</strong></div>
    </div>

    <div class="charts">
      <div>
        <h3>Agent 작업 결과</h3>
        <div v-if="tasks.length" class="donut-wrap">
          <svg viewBox="0 0 42 42" class="donut" role="img" :aria-label="`작업 ${tasks.length}건`">
            <circle cx="21" cy="21" r="15.915" fill="none" stroke="var(--border)" stroke-width="6" />
            <circle v-for="s in slices" :key="s.key" cx="21" cy="21" r="15.915" fill="none" :stroke="s.color"
                    stroke-width="6" :stroke-dasharray="s.dash" :stroke-dashoffset="s.offset" />
            <text x="21" y="22.5" text-anchor="middle" class="donut-total">{{ tasks.length }}</text>
          </svg>
          <ul class="legend">
            <li v-for="o in outcomes" :key="o.key">
              <span class="swatch" :style="{ background: o.color }"></span>{{ o.label }} {{ o.count }}
            </li>
          </ul>
        </div>
        <p v-else class="muted">아직 작업이 없다</p>
      </div>

      <div>
        <h3>에러 유형</h3>
        <ul v-if="errorTypes.length" class="bars">
          <li v-for="[type, n] in errorTypes" :key="type">
            <span class="bar-label">{{ type }}</span>
            <span class="bar-track"><span class="bar" :style="{ width: `${(n / maxErrorType) * 100}%` }"></span></span>
            <span class="bar-count">{{ n }}</span>
          </li>
        </ul>
        <p v-else class="muted">분류된 작업이 없다</p>
      </div>
    </div>
  </section>
</template>

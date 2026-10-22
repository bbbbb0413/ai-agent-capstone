<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { api } from '../api.js'
import { clockTime } from '../format.js'

const props = defineProps({ tenants: { type: Array, required: true } })
const tenant = ref('')
const data = ref(null)
const selected = ref('')
const focused = ref('')
const error = ref('')
const busy = ref(false)
let generation = 0
let timer
const labels = { HEALTHY: '관측 정상', ABNORMAL: '이상 관측', DEGRADED: '호출 지연', HIGH_LATENCY: '심한 호출 지연', UNKNOWN: '확인 불가' }
const kinds = { database: 'DB', external_api: '외부 API', internal_service: '내부 서비스' }
const tone = (status) => status === 'HEALTHY' ? 'healthy' : status === 'UNKNOWN' ? 'unknown' : 'abnormal'
const number = (v, unit = '') => Number.isFinite(v) ? `${v.toFixed(2)}${unit}` : '—'
const percent = (v) => Number.isFinite(v) ? `${(v * 100).toFixed(1)}%` : '—'
const dependency = computed(() => data.value?.dependencies.find(d => d.id === selected.value))
const consumers = computed(() => (dependency.value?.consumers || []).map(c => ({ ...c, ...data.value.services.find(s => s.name === c.service) })))
const affected = computed(() => new Set(consumers.value.map(c => c.service)))
const counts = computed(() => ({ abnormal: consumers.value.filter(c => c.status === 'ABNORMAL').length,
  healthy: consumers.value.filter(c => c.status === 'HEALTHY').length,
  unknown: consumers.value.filter(c => c.status === 'UNKNOWN').length }))
const height = computed(() => Math.max(data.value?.services.length || 0, data.value?.dependencies.length || 0, 1) * 86 + 54)
const edges = computed(() => (data.value?.edges || []).map(e => ({ ...e,
  start: data.value.services.findIndex(s => s.name === e.service) * 86 + 77,
  end: data.value.dependencies.findIndex(d => d.id === e.dependencyId) * 86 + 77,
})))
const details = computed(() => data.value?.services.find(s => s.name === focused.value))

async function load() {
  if (!tenant.value) return
  const current = ++generation
  busy.value = true
  try {
    const response = await api.impact(tenant.value)
    if (current !== generation) return
    data.value = response
    error.value = ''
    if (!response.dependencies.some(d => d.id === selected.value)) selected.value = response.dependencies[0]?.id || ''
    if (!response.services.some(s => s.name === focused.value)) focused.value = ''
  } catch (e) {
    if (current !== generation) return
    data.value = null // 오래된 관측값을 현재 정상 상태로 보여주지 않는다
    error.value = e.message
  } finally {
    if (current === generation) busy.value = false
  }
}
watch(tenant, () => { ++generation; busy.value = false; data.value = null; selected.value = ''; focused.value = ''; error.value = ''; load() })
watch(() => props.tenants, (items) => {
  if (!items.some(t => t.id === tenant.value)) tenant.value = items[0]?.id || ''
}, { immediate: true })
onMounted(() => { timer = setInterval(() => { if (!document.hidden && !busy.value) load() }, 30_000) })
onUnmounted(() => { ++generation; clearInterval(timer) })
</script>

<template>
  <section class="card impact-view">
    <div class="impact-heading">
      <div><h2>서비스 의존성과 장애 영향</h2><p class="muted">의존 대상을 선택해 연결된 서비스와 현재 관측 상태를 확인하세요.</p></div>
      <div class="row">
        <label>고객사<select v-model="tenant"><option v-for="t in tenants" :key="t.id" :value="t.id">{{ t.name }}</option></select></label>
        <button :disabled="busy || !tenant" @click="load">{{ busy ? '조회 중…' : '새로고침' }}</button>
      </div>
    </div>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="busy && !data" role="status">서비스 연결 정보와 최근 지표를 조회하고 있습니다.</p>
    <template v-if="data">
      <p class="muted">{{ data.window }} · {{ clockTime(data.observedAt) }} 기준 · 30초마다 갱신</p>
      <p v-for="warning in data.warnings" :key="warning" class="impact-warning" role="status">{{ warning }}</p>
      <p v-if="!data.services.length" class="empty muted">등록된 서비스가 없습니다.</p>
      <template v-else>
        <p class="impact-explanation">선은 등록된 호출 관계입니다. 연결된 서비스는 영향 가능성이 있지만, 지표 이상만으로 선택한 대상이 원인이라고 확정하지 않습니다. 데이터가 없거나 호출이 없으면 확인 불가입니다.</p>
        <div class="impact-legend"><span class="healthy">● 관측 정상</span><span class="abnormal">● 이상·지연 관측</span><span class="unknown">● 확인 불가</span><span>서비스 → 의존 대상</span></div>
        <div class="impact-map" aria-label="서비스 의존성 연결도">
          <svg :viewBox="`0 0 780 ${height}`" :style="{ height: `${height}px` }" role="group" aria-label="서비스와 의존 대상. 노드를 선택하면 상세 정보가 표시됩니다.">
            <defs><marker id="impact-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" /></marker></defs>
            <text x="24" y="25" class="impact-column">서비스 · 클릭하여 지표 보기</text><text x="506" y="25" class="impact-column">의존 대상 · 클릭하여 영향 범위 보기</text>
            <path v-for="e in edges" :key="`${e.service}:${e.dependencyId}`" :d="`M 274 ${e.start} C 365 ${e.start}, 410 ${e.end}, 502 ${e.end}`"
              fill="none" :class="['impact-edge', tone(e.status), { chosen: e.dependencyId === selected }]" marker-end="url(#impact-arrow)">
              <title>{{ e.service }} → {{ data.dependencies.find(d => d.id === e.dependencyId)?.name }}: {{ labels[e.status] }}, p95 {{ number(e.p95Seconds, '초') }}</title>
            </path>
            <g v-for="(s, i) in data.services" :key="s.name" :transform="`translate(24, ${i * 86 + 46})`"
               :class="['impact-node', tone(s.status), { chosen: affected.has(s.name) }]" role="button" tabindex="0"
               :aria-label="`${s.name}: ${labels[s.status]}, 지표 보기`" @click="focused = s.name" @keydown.enter="focused = s.name" @keydown.space.prevent="focused = s.name">
              <rect width="250" height="62" rx="8" /><text x="14" y="25">{{ s.name.length > 27 ? s.name.slice(0, 25) + '…' : s.name }}</text><text x="14" y="47" class="impact-subtext">{{ labels[s.status] }}</text><title>{{ s.name }}</title>
            </g>
            <g v-for="(d, i) in data.dependencies" :key="d.id" :transform="`translate(506, ${i * 86 + 46})`"
               :class="['impact-node', tone(d.status), { chosen: selected === d.id }]" role="button" tabindex="0" :aria-pressed="selected === d.id"
               :aria-label="`${d.name}: ${labels[d.status]}, 영향 범위 보기`" @click="selected = d.id" @keydown.enter="selected = d.id" @keydown.space.prevent="selected = d.id">
              <rect width="250" height="62" rx="8" /><text x="14" y="25">{{ d.name.length > 27 ? d.name.slice(0, 25) + '…' : d.name }}</text><text x="14" y="47" class="impact-subtext">{{ kinds[d.kind] || d.kind }} · {{ labels[d.status] }}</text><title>{{ d.name }}</title>
            </g>
          </svg>
        </div>
        <p class="muted">의존 대상 색상은 각 서비스에서 측정한 호출 지연을 종합한 값입니다. 대상 자체의 장애 확정이나 가동 여부를 뜻하지 않습니다.</p>
        <div v-if="details" class="impact-detail">
          <h3>{{ details.name }} · {{ labels[details.status] }}</h3>
          <p>에러율 {{ percent(details.errorRate) }} · p95 {{ number(details.p95Seconds, '초') }} · 요청 {{ number(details.requestsPerSecond, '건/초') }}</p>
          <p class="muted">요청이 있을 때 에러율 5% 이상 또는 p95 1초 이상이면 이상 관측으로 표시합니다.</p>
        </div>
        <template v-if="dependency">
          <div class="impact-heading"><h2>{{ dependency.name }} 연결 서비스</h2><label>의존 대상<select v-model="selected"><option v-for="d in data.dependencies" :key="d.id" :value="d.id">{{ d.name }} ({{ kinds[d.kind] || d.kind }})</option></select></label></div>
          <p>영향 가능 범위 <strong>{{ consumers.length }}개</strong> · 이상 관측 {{ counts.abnormal }}개 · 관측 정상 {{ counts.healthy }}개 · 확인 불가 {{ counts.unknown }}개</p>
          <div class="impact-table"><table class="grid-table"><thead><tr><th>서비스</th><th>연결 경로</th><th>서비스 관측 상태</th><th>에러율</th><th>p95</th><th>대상 호출 p95</th></tr></thead><tbody>
            <tr v-for="s in consumers" :key="s.service"><td><button class="link" @click="focused = s.service">{{ s.service }}</button></td><td>{{ s.path.join(' → ') }}<br><span class="muted">{{ s.direct ? '직접 연결' : '간접 연결' }}</span></td><td :class="tone(s.status)">{{ labels[s.status] }}</td><td>{{ percent(s.errorRate) }}</td><td>{{ number(s.p95Seconds, '초') }}</td><td>{{ s.direct ? number(data.edges.find(e => e.service === s.service && e.dependencyId === selected)?.p95Seconds, '초') : '간접 연결' }}</td></tr>
          </tbody></table></div>
          <p class="muted">직접·간접 연결은 같은 고객사의 등록된 관계만 추적합니다. 내부 서비스 관계가 여러 개면 가장 짧은 경로 하나를 표시합니다.</p>
        </template>
        <p v-else class="muted">등록된 의존 관계가 없습니다. 서비스 상태만 표시합니다.</p>
      </template>
    </template>
  </section>
</template>

<style scoped>
.impact-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.impact-heading .row { align-items: end; }
.impact-heading p { margin: 4px 0; }
.impact-warning { padding: 12px; background: var(--warn-bg); color: var(--warn); border-radius: 4px; }
.impact-explanation { max-width: 880px; line-height: 1.7; }
.impact-legend { display: flex; gap: 20px; flex-wrap: wrap; font-size: 13px; margin: 20px 0 8px; }
.healthy { color: var(--ok); }
.unknown { color: var(--muted); }
.abnormal { color: var(--danger); }
.impact-map { overflow: auto; max-height: 540px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg); }
svg { display: block; width: 100%; min-width: 780px; }
.impact-column { fill: var(--muted); font-size: 13px; }
.impact-node { cursor: pointer; }
.impact-node rect { fill: var(--surface); stroke: currentColor; stroke-width: 1.5; }
.impact-node.chosen rect, .impact-node:focus rect { stroke-width: 3; }
.impact-node text { fill: var(--text); font-size: 14px; font-weight: 600; }
.impact-node .impact-subtext { fill: currentColor; font-size: 12px; font-weight: 400; }
.impact-edge { stroke: currentColor; stroke-width: 1.5; opacity: .35; }
.impact-edge.chosen { opacity: 1; stroke-width: 2.5; }
.impact-edge.unknown { stroke-dasharray: 5 4; }
.impact-detail { padding: 16px; border-left: 3px solid var(--accent); margin: 20px 0; background: var(--bg); }
.impact-detail h3 { color: var(--text); }
.impact-table { overflow: auto; margin: 16px -18px; }
.impact-table td { white-space: normal; min-width: 100px; }
</style>

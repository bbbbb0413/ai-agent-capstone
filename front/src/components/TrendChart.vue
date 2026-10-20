<script setup>
import { computed, reactive, ref } from 'vue'
import { clockTime, shortDateTime } from '../format.js'
import AppIcon from './AppIcon.vue'

const props = defineProps({
  incidents: { type: Array, required: true },
  tasks: { type: Array, required: true },
  updatedAt: { type: Date, default: null },
})
const emit = defineEmits(['refresh'])

const RANGES = [
  { key: '6h', label: '6시간', hours: 6, stepMinutes: 30 },
  { key: '24h', label: '24시간', hours: 24, stepMinutes: 60 },
  { key: '7d', label: '7일', hours: 168, stepMinutes: 360 },
]
const SERIES = [
  { key: 'incidents', label: '인시던트 발생', color: 'var(--danger)' },
  { key: 'tasks', label: 'Agent 작업', color: 'var(--accent)' },
  { key: 'failed', label: '실패한 작업', color: 'var(--warn)' },
]
const W = 800
const H = 240
const PAD = { left: 36, right: 12, top: 12, bottom: 28 }

const range = ref(RANGES[1])
const offset = ref(0)   // 몇 구간 앞을 보는지 (0이면 지금까지)
const shown = reactive(Object.fromEntries(SERIES.map((s) => [s.key, true])))

// 구간 경계를 현지 시각의 정각(30분·1시간·6시간 단위)에 맞춘다
const frame = computed(() => {
  const step = range.value.stepMinutes * 60_000
  const tz = -new Date().getTimezoneOffset() * 60_000
  const base = (props.updatedAt ?? new Date()).getTime()
  const end = Math.ceil((base + tz) / step) * step - tz - offset.value * range.value.hours * 3_600_000
  const count = (range.value.hours * 60) / range.value.stepMinutes
  return { step, end, start: end - count * step, count }
})

function bucketize(times) {
  const { start, step, count } = frame.value
  const buckets = new Array(count).fill(0)
  for (const t of times) {
    const i = Math.floor((t - start) / step)
    if (i >= 0 && i < count) buckets[i]++
  }
  return buckets
}

const data = computed(() => ({
  incidents: bucketize(props.incidents.map((i) => new Date(i.startedAt).getTime())),
  tasks: bucketize(props.tasks.map((t) => new Date(t.createdAt).getTime())),
  failed: bucketize(props.tasks.filter((t) => t.status === 'FAILED').map((t) => new Date(t.createdAt).getTime())),
}))

const yMax = computed(() => {
  const max = Math.max(0, ...SERIES.filter((s) => shown[s.key]).flatMap((s) => data.value[s.key]))
  return Math.max(4, Math.ceil(max / 4) * 4)
})
const yTicks = computed(() => [0, 1, 2, 3, 4].map((k) => (yMax.value / 4) * k))

const plotW = W - PAD.left - PAD.right
const plotH = H - PAD.top - PAD.bottom
const x = (i) => PAD.left + ((i + 0.5) * plotW) / frame.value.count
const y = (v) => PAD.top + (1 - v / yMax.value) * plotH

const lines = computed(() =>
  SERIES.filter((s) => shown[s.key]).map((s) => ({
    ...s,
    points: data.value[s.key].map((v, i) => `${x(i)},${y(v)}`).join(' '),
    dots: data.value[s.key].map((v, i) => ({ i, v })).filter((d) => d.v > 0),
  })),
)

const fmt = (t, withDate) => (withDate ? shortDateTime(t) : clockTime(t, false))
const xLabels = computed(() => {
  const { count, start, step } = frame.value
  const every = Math.ceil(count / 6)
  const withDate = range.value.hours > 24
  return Array.from({ length: count }, (_, i) => i)
    .filter((i) => i % every === 0)
    .map((i) => ({ x: x(i), text: fmt(start + i * step, withDate) }))
})
const period = computed(() => `${fmt(frame.value.start, true)} ~ ${fmt(frame.value.end, true)}`)

const stats = computed(() =>
  SERIES.map((s) => {
    const values = data.value[s.key]
    return { ...s, total: values.reduce((a, b) => a + b, 0), max: Math.max(...values), last: values.at(-1) }
  }),
)

function pickRange(r) {
  range.value = r
  offset.value = 0
}
</script>

<template>
  <section class="card trend">
    <div class="toolbar">
      <button class="icon-btn" title="새로고침" @click="emit('refresh')"><AppIcon name="refresh" :size="16" /></button>
      <div class="segmented">
        <button v-for="r in RANGES" :key="r.key" :class="{ active: r.key === range.key }" @click="pickRange(r)">{{ r.label }}</button>
      </div>
      <span class="muted toolbar-note">
        <AppIcon name="pulse" :size="14" /> 실시간 갱신{{ updatedAt ? ` · ${clockTime(updatedAt)}` : '' }}
      </span>
    </div>

    <svg :viewBox="`0 0 ${W} ${H}`" class="trend-svg" role="img" :aria-label="`${period} 추이`">
      <g class="grid">
        <g v-for="t in yTicks" :key="t">
          <line :x1="PAD.left" :x2="W - PAD.right" :y1="y(t)" :y2="y(t)" />
          <text :x="PAD.left - 8" :y="y(t) + 4" text-anchor="end">{{ t }}</text>
        </g>
        <text v-for="l in xLabels" :key="l.x" :x="l.x" :y="H - 8" text-anchor="middle">{{ l.text }}</text>
      </g>
      <g v-for="line in lines" :key="line.key">
        <polyline :points="line.points" fill="none" :stroke="line.color" stroke-width="2" stroke-linejoin="round" />
        <circle v-for="d in line.dots" :key="d.i" :cx="x(d.i)" :cy="y(d.v)" r="3.5" :fill="line.color">
          <title>{{ fmt(frame.start + d.i * frame.step, true) }} · {{ line.label }} {{ d.v }}건</title>
        </circle>
      </g>
    </svg>

    <div class="pager">
      <button class="icon-btn" title="이전 구간" @click="offset++"><AppIcon name="left" :size="16" /></button>
      <span>{{ period }}</span>
      <button class="icon-btn" title="다음 구간" :disabled="offset === 0" @click="offset--"><AppIcon name="right" :size="16" /></button>
    </div>

    <table class="legend-table">
      <thead>
        <tr><th>선</th><th>표시</th><th>항목</th><th class="num">합계</th><th class="num">구간 최대</th><th class="num">마지막 구간</th></tr>
      </thead>
      <tbody>
        <tr v-for="s in stats" :key="s.key">
          <td><span class="line-swatch" :style="{ background: s.color }"></span></td>
          <td><input v-model="shown[s.key]" type="checkbox" :aria-label="`${s.label} 표시`" /></td>
          <td>{{ s.label }}</td>
          <td class="num">{{ s.total }}</td>
          <td class="num">{{ s.max }}</td>
          <td class="num">{{ s.last }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup>
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api.js'
import StatusBadge from '../components/StatusBadge.vue'
import SlackEscalations from '../components/SlackEscalations.vue'
import TaskAttachments from '../components/TaskAttachments.vue'
import TaskRetry from '../components/TaskRetry.vue'
import { ADMIN_ONLY_TOOLS, ESCALATION_CATEGORIES, actionDetail, actionResultLabel, approvalChangeMode, approvalFields, approvalImpact, approvalImpactFields, recipientsLabel, toolLabel, toTimeline } from '../progress.js'
import { useSession } from '../session.js'

const props = defineProps({ id: { type: String, required: true } })
const session = useSession()
const task = ref(null)
const answer = ref('')
const events = ref([])     // 저장된 진행 이력 + 실시간 이벤트
const approval = ref(null)
const change = ref(null)   // GitOps PR 병합·동기화 대기 (CHANGE_PENDING)
const error = ref('')
const deciding = ref(false)
// 진행 중에는 스트리밍되는 리포트를 보여주고, 끝나서 요약 표가 생기면 접어 둔다
const reportOpen = ref(true)
let source

// 리포트에는 모델 출력과 로그 원문이 섞이므로 HTML로 그리기 전에 반드시 정리한다
const reportHtml = computed(() => DOMPurify.sanitize(marked.parse(task.value?.answer || answer.value)))

const adminOnly = computed(() => ADMIN_ONLY_TOOLS.includes(approval.value?.tool))
const timeline = computed(() => toTimeline(events.value))
const startedAt = computed(() => new Date(task.value?.createdAt || events.value[0]?.createdAt || Date.now()))
const STATE_MARKS = { running: '', done: '✓', wait: '…', ok: '✓', fail: '✕' }
const elapsed = (at) => `+${Math.max(0, Math.round((new Date(at) - startedAt.value) / 1000))}초`

const summary = computed(() => task.value?.summary)
const DECISIONS = { APPROVE: '승인', REJECT: '거절' }
const CHANGE_RESULTS = { SYNCED: '병합·동기화 완료', TIMEOUT: '기한 안에 병합되지 않음' }

const actionLabel = computed(() => {
  const action = summary.value?.action
  if (!action) return '제안 없음'
  if (action.tool === 'business_send_customer_notice') return '고객 공지 발송'
  if (action.tool === 'business_escalate_incident') return '조치 대신 에스컬레이션'
  return actionDetail(action.args) || action.tool
})
const recoveredLabel = computed(() => {
  const recovered = summary.value?.recovered
  return recovered === true ? '회복' : recovered === false ? '미회복' : '확인 안 함'
})
const decisionLabel = (decision, none = '해당 없음') => DECISIONS[decision] || none

async function refresh() {
  task.value = await api.task(props.id)
  reportOpen.value = !task.value.summary
}

// 진행 이벤트: 화면 영역마다 필요한 이벤트만 받는다 (기획안 5.3)
function subscribe() {
  source = api.stream(props.id)
  source.addEventListener('TOKEN', (e) => (answer.value += JSON.parse(e.data).text))
  const record = (type, handle) =>
    source.addEventListener(type, (e) => {
      const data = JSON.parse(e.data)
      events.value.push({ type, data, createdAt: new Date().toISOString() })
      handle?.(data)
    })
  const setStatus = (status) => task.value && (task.value.status = status)
  record('STEP', () => setStatus('RUNNING'))
  record('TOOL_START', () => setStatus('RUNNING'))
  record('TOOL_END')
  record('APPROVAL_REQUIRED', (data) => {
    approval.value = data
    setStatus('WAITING_APPROVAL')
  })
  record('APPROVAL_DECIDED', () => {
    approval.value = null
    setStatus('RUNNING')
  })
  record('CHANGE_PENDING', (data) => {
    change.value = data
    setStatus('CHANGE_PENDING')
  })
  record('CHANGE_FINISHED', () => {
    change.value = null
    setStatus('RUNNING')
  })
  for (const type of ['DONE', 'FAILED']) {
    record(type, async () => {
      source.close()
      await refresh()
    })
  }
}

onMounted(async () => {
  try {
    await refresh()
    events.value = await api.taskEvents(props.id)
    if (task.value.status === 'WAITING_APPROVAL') approval.value = task.value.pendingAction
    if (task.value.status === 'CHANGE_PENDING') change.value = task.value.pendingAction
    if (!['DONE', 'FAILED'].includes(task.value.status)) subscribe()
  } catch (e) {
    error.value = e.message
  }
})
onUnmounted(() => source?.close())

async function decide(decision) {
  if (deciding.value) return
  deciding.value = true
  error.value = ''
  try {
    await api.approve(props.id, decision, undefined, approval.value?.approvalId)
    approval.value = null
    await refresh()
    if (task.value.status === 'WAITING_APPROVAL') approval.value = task.value.pendingAction
    if (!['DONE', 'FAILED'].includes(task.value.status)
        && (!source || source.readyState === EventSource.CLOSED)) subscribe()
  } catch (e) {
    error.value = e.message
  } finally {
    deciding.value = false
  }
}
</script>

<template>
  <RouterLink to="/" class="back">← 인시던트 목록</RouterLink>
  <p v-if="error" class="error">{{ error }}</p>
  <section v-if="task" class="card">
    <h2>작업 {{ task.type === 'ATTACHMENT' ? '첨부 자료 분석' : task.type }} · {{ task.tenantId }} <StatusBadge kind="task" :value="task.status" /></h2>
    <p class="muted">{{ task.question }}</p>
    <TaskRetry :task="task" :is-admin="session.isAdmin" />
  </section>

  <TaskAttachments v-if="task?.type === 'ATTACHMENT' && session.isMspStaff" :task-id="id" />

  <section v-if="approval" class="card approval">
    <h2>승인 필요: {{ toolLabel(approval.tool) }}</h2>
    <p v-if="approval.reason">{{ approval.reason }}</p>
    <table class="summary approval-fields">
      <tbody>
        <tr v-for="[name, value] in approvalFields(approval)" :key="name">
          <th>{{ name }}</th><td class="pre-line">{{ value }}</td>
        </tr>
        <tr v-for="[name, value] in approvalImpactFields(approval)" :key="`impact-${name}`">
          <th>{{ name }}</th><td>{{ value }}</td>
        </tr>
        <tr v-if="approval.changeMode">
          <th>반영 방식</th>
          <td>{{ approvalChangeMode(approval) }}</td>
        </tr>
        <tr v-if="approvalImpact(approval)"><th>영향</th><td>{{ approvalImpact(approval) }}</td></tr>
      </tbody>
    </table>
    <details v-if="approval.runbook?.length">
      <summary>근거 문서</summary>
      <ul><li v-for="d in approval.runbook" :key="d.uri + d.section">{{ d.title }} · {{ d.section }}</li></ul>
    </details>
    <details>
      <summary>원본 인자</summary>
      <pre>{{ JSON.stringify(approval.args, null, 2) }}</pre>
    </details>
    <template v-if="session.isMspStaff">
      <p v-if="adminOnly && !session.isAdmin" class="muted">
        이 조치는 MSP 관리자만 승인하거나 거절할 수 있다. 관리자에게 확인을 요청한다.
      </p>
      <div v-else class="row">
        <button :disabled="deciding" @click="decide('APPROVE')">{{ deciding ? '전달 중…' : '승인' }}</button>
        <button class="secondary" :disabled="deciding" @click="decide('REJECT')">거절</button>
      </div>
    </template>
  </section>

  <SlackEscalations v-if="task?.incidentId && session.isMspStaff" :incident-id="task.incidentId" />

  <section v-if="change && task?.status !== 'DONE'" class="card approval">
    <h2>반영 대기: {{ change.action }} · {{ change.service }}</h2>
    <p>설정 저장소 PR이 병합되고 ArgoCD 동기화가 끝나면 회복 확인을 이어서 한다.</p>
    <a :href="change.prUrl" target="_blank" rel="noopener">PR #{{ change.prNumber }} 열기</a>
  </section>

  <section v-if="summary" class="card">
    <h2>요약</h2>
    <table class="summary">
      <tbody>
        <tr><th>에러 유형</th><td><span class="tag">{{ summary.error_type }}</span></td></tr>
        <tr v-if="summary.severity || summary.service">
          <th>심각도 · 서비스</th><td>{{ [summary.severity, summary.service].filter(Boolean).join(' · ') }}</td>
        </tr>
        <tr><th>원인</th><td>{{ summary.cause }}</td></tr>
        <tr><th>근거 조항</th><td>{{ summary.runbook_clause || '없음' }}</td></tr>
        <tr><th>조치</th><td>{{ actionLabel }}</td></tr>
        <tr v-if="summary.action"><th>조치 승인</th><td>{{ decisionLabel(summary.action.decision, '결정 전') }}</td></tr>
        <tr v-if="summary.action">
          <th>실행 결과</th>
          <td :class="{ error: summary.action.result?.status === 'FAILED' }">
            {{ actionResultLabel(summary.action.result) }}
            <p v-if="summary.action.result?.error" class="muted">{{ summary.action.result.error }}</p>
          </td>
        </tr>
        <tr v-if="summary.change">
          <th>반영</th>
          <td>
            <a :href="summary.change.prUrl" target="_blank" rel="noopener">PR #{{ summary.change.prNumber }}</a>
            · {{ CHANGE_RESULTS[summary.change.result] || '반영 대기' }}
          </td>
        </tr>
        <tr>
          <th>회복 여부</th>
          <td :class="{ ok: summary.recovered === true, error: summary.recovered === false }">{{ recoveredLabel }}</td>
        </tr>
        <tr><th>에스컬레이션</th><td>{{ decisionLabel(summary.escalation) }}</td></tr>
        <tr v-if="summary.escalation_result">
          <th>호출</th>
          <td :class="{ error: summary.escalation_result.status === 'FAILED' }">
            {{ actionResultLabel(summary.escalation_result) }}
            <span v-if="summary.escalation_result.category"> · {{ ESCALATION_CATEGORIES[summary.escalation_result.category] }}</span>
            <p v-if="summary.escalation_result.recipients?.length" class="muted">
              받는 사람: {{ recipientsLabel(summary.escalation_result.recipients) }}
            </p>
            <p v-if="summary.escalation_result.error" class="muted">{{ summary.escalation_result.error }}</p>
          </td>
        </tr>
        <tr><th>고객 공지</th><td>{{ decisionLabel(summary.notice) }}</td></tr>
        <tr v-if="summary.notice_result"><th>공지 결과</th><td>{{ actionResultLabel(summary.notice_result) }}</td></tr>
      </tbody>
    </table>
  </section>

  <section class="card">
    <h2>진행 상황</h2>
    <ol v-if="timeline.length" class="timeline">
      <li v-for="(row, i) in timeline" :key="i" :class="row.state">
        <span class="mark">{{ STATE_MARKS[row.state] }}</span>
        <span class="time" :title="new Date(row.at).toLocaleString()">{{ elapsed(row.at) }}</span>
        <span class="stage">{{ row.stage !== timeline[i - 1]?.stage ? row.stage : '' }}</span>
        <span class="what">
          {{ row.label }}<template v-if="row.state === 'running' && row.kind === 'tool'"> 중…</template>
          <a v-if="row.link" :href="row.link" target="_blank" rel="noopener">열기</a>
          <span v-if="row.detail" class="muted detail" :title="row.detail">{{ row.detail }}</span>
        </span>
      </li>
    </ol>
    <p v-else class="muted">
      {{ ['DONE', 'FAILED'].includes(task?.status) ? '진행 기록이 없다 (진행 이력 저장 전에 끝난 작업)' : '시작을 기다리는 중…' }}
    </p>
  </section>

  <section class="card">
    <details class="collapsible" :open="reportOpen" @toggle="reportOpen = $event.target.open">
      <summary><h2>리포트</h2></summary>
      <div class="report markdown" v-html="reportHtml"></div>
      <ul v-if="task?.sources?.length" class="muted">
        <li v-for="s in task.sources" :key="s.doc + s.section">근거: {{ s.doc }} · {{ s.section }}</li>
      </ul>
    </details>
    <a v-if="task?.traceUrl" :href="task.traceUrl" target="_blank" rel="noopener">trace 보기</a>
  </section>
</template>

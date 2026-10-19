<script setup>
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api.js'
import { useSession } from '../session.js'

const props = defineProps({ id: { type: String, required: true } })
const session = useSession()
const task = ref(null)
const answer = ref('')
const activity = ref([])
const approval = ref(null)
const change = ref(null)   // GitOps PR 병합·동기화 대기 (CHANGE_PENDING)
const error = ref('')
// 진행 중에는 스트리밍되는 리포트를 보여주고, 끝나서 요약 표가 생기면 접어 둔다
const reportOpen = ref(true)
let source

// 리포트에는 모델 출력과 로그 원문이 섞이므로 HTML로 그리기 전에 반드시 정리한다
const reportHtml = computed(() => DOMPurify.sanitize(marked.parse(task.value?.answer || answer.value)))

const summary = computed(() => task.value?.summary)
const RUNBOOK_ACTIONS = {
  restart_pods: '파드 재시작',
  scale_deployment: '레플리카 확장',
  rollback_deployment: '직전 배포로 롤백',
}
const DECISIONS = { APPROVE: '승인', REJECT: '거절' }
const CHANGE_RESULTS = { SYNCED: '병합·동기화 완료', TIMEOUT: '기한 안에 병합되지 않음' }

const actionLabel = computed(() => {
  const action = summary.value?.action
  if (!action) return '제안 없음'
  if (action.tool === 'business_send_customer_notice') return '고객 공지 발송'
  const { action: kind, service, replicas } = action.args || {}
  const name = RUNBOOK_ACTIONS[kind] || kind || action.tool
  const detail = [service, replicas != null ? `replicas=${replicas}` : null].filter(Boolean).join(', ')
  return detail ? `${name} (${detail})` : name
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
  source.addEventListener('TOOL_START', (e) => activity.value.push(`${JSON.parse(e.data).tool} 실행 중…`))
  source.addEventListener('TOOL_END', (e) => activity.value.push(`${JSON.parse(e.data).tool} 완료`))
  source.addEventListener('APPROVAL_REQUIRED', (e) => (approval.value = JSON.parse(e.data)))
  source.addEventListener('CHANGE_PENDING', (e) => (change.value = JSON.parse(e.data)))
  for (const type of ['DONE', 'FAILED']) {
    source.addEventListener(type, async () => {
      source.close()
      await refresh()
    })
  }
}

onMounted(async () => {
  try {
    await refresh()
    if (task.value.status === 'WAITING_APPROVAL') approval.value = task.value.pendingAction
    if (task.value.status === 'CHANGE_PENDING') change.value = task.value.pendingAction
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
  <RouterLink to="/" class="back">← 인시던트 목록</RouterLink>
  <p v-if="error" class="error">{{ error }}</p>
  <section v-if="task" class="card">
    <h2>작업 {{ task.type }} · {{ task.tenantId }} · {{ task.status }}</h2>
    <p class="muted">{{ task.question }}</p>
  </section>

  <section v-if="approval" class="card approval">
    <h2>승인 필요: {{ approval.tool }}</h2>
    <p>{{ approval.reason }}</p>
    <p v-if="approval.changeMode" class="muted">
      반영 방식: {{ approval.changeMode === 'GITOPS' ? '설정 저장소 PR (병합 후 ArgoCD 동기화)' : 'Kubernetes API로 바로 실행' }}
    </p>
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
        <tr><th>고객 공지</th><td>{{ decisionLabel(summary.notice) }}</td></tr>
      </tbody>
    </table>
  </section>

  <section class="card">
    <h2>진행 상황</h2>
    <ul class="activity"><li v-for="(line, i) in activity" :key="i">{{ line }}</li></ul>
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

<script setup>
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { api } from '../api.js'
import { slackStatus, canReviewSlack } from '../slack.js'
import { useSession } from '../session.js'
import { shortDateTime } from '../format.js'

const props = defineProps({ incidentId: { type: Number, required: true } })
const escalations = ref([])
const deliveries = ref([])
const tracking = ref([])
const session = useSession()
const trackingLabels = { WAITING: '미확인', ACKNOWLEDGED: '확인됨', IN_PROGRESS: '담당 처리 중', COMPLETED: '처리 완료' }
const actionLabels = { ACK: '확인', START: '담당 시작', COMPLETE: '처리 완료' }
const handling = (id) => tracking.value.find(t => t.deliveryId === delivery(id)?.id)
const preview = ref(null)
const message = ref('')
const busy = ref(false)
const error = ref('')
let timer
let generation = 0

const delivery = (id) => deliveries.value.find((d) => d.escalationId === id)
async function load() {
  const current = ++generation
  try {
    const result = await Promise.all([
      api.incidentEscalations(props.incidentId), api.slackDeliveries(props.incidentId),
      api.slackTracking(props.incidentId),
    ])
    if (current !== generation) return
    ;[escalations.value, deliveries.value, tracking.value] = result
  } catch (e) { if (current === generation) error.value = e.message }
}
onMounted(() => { load(); timer = setInterval(() => document.hidden || load(), 5000) })
onUnmounted(() => { ++generation; clearInterval(timer) })
watch(() => props.incidentId, () => { ++generation; preview.value = null; escalations.value = []; deliveries.value = []; tracking.value = []; load() })

async function act(id, action) {
  busy.value = true; error.value = ''
  try { await api.updateSlackTracking(props.incidentId, delivery(id).id, action); await load() }
  catch (e) { error.value = e.message }
  finally { busy.value = false }
}

async function review(id) {
  busy.value = true
  error.value = ''
  try {
    preview.value = await api.slackPreview(props.incidentId, id)
    message.value = preview.value.message
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}

async function send() {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    await api.approveSlack(props.incidentId, preview.value.escalationId, {
      message: message.value, connectionVersion: preview.value.connection.version,
    })
    preview.value = null
    await load()
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}
</script>

<template>
  <section v-if="escalations.length || error" class="card">
    <h2>Slack 발송과 담당자 처리</h2>
    <p>내부 담당자에게 전달할 호출 기록입니다. 문안을 검토한 뒤 발송하세요.</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <ul class="notices">
      <li v-for="e in escalations" :key="e.id">
        <strong>{{ e.reason }}</strong>
        <p>{{ slackStatus(delivery(e.id)?.status) }}
          <span v-if="delivery(e.id)"> · {{ delivery(e.id).channelLabel }} · 승인자 {{ delivery(e.id).approvedBy }}</span>
        </p>
        <p v-if="delivery(e.id)?.lastError" class="muted">{{ delivery(e.id).lastError }}</p>
        <button v-if="canReviewSlack(delivery(e.id)?.status)" :disabled="busy" @click="review(e.id)">Slack 발송 검토</button>
        <details v-if="delivery(e.id)"><summary>승인한 문안</summary><p class="pre-line">{{ delivery(e.id).message }}</p></details>
        <div v-if="handling(e.id) && delivery(e.id)?.status === 'SENT'" class="tracking-card">
          <p><strong>{{ trackingLabels[handling(e.id).status] }}</strong>
            <span v-if="handling(e.id).acknowledgedBy"> · 확인자 {{ handling(e.id).acknowledgedBy }}</span>
            <span v-if="handling(e.id).assignedTo"> · 담당자 {{ handling(e.id).assignedTo }}</span>
            <span v-if="handling(e.id).completedBy"> · 완료자 {{ handling(e.id).completedBy }}</span></p>
          <div class="row">
            <button v-if="handling(e.id).status === 'WAITING'" :disabled="busy" @click="act(e.id,'ACK')">확인했습니다</button>
            <button v-if="['WAITING','ACKNOWLEDGED'].includes(handling(e.id).status)" :disabled="busy" @click="act(e.id,'START')">담당 시작</button>
            <button v-if="handling(e.id).status === 'IN_PROGRESS' && (session.isAdmin || handling(e.id).assignedTo === session.user?.username)" :disabled="busy" @click="act(e.id,'COMPLETE')">처리 완료</button>
          </div>
          <p class="muted">담당자의 처리 기록이며, 실제 장애 복구 상태와는 별개입니다. Slack 버튼으로 처리하면 메시지가 갱신됩니다. 웹에서 변경한 내용은 이 화면에서 확인하세요.</p>
          <ol v-if="handling(e.id).events.length"><li v-for="(event,i) in handling(e.id).events" :key="i">{{ shortDateTime(event.createdAt) }} · {{ event.actor }} · {{ actionLabels[event.action] }} ({{ event.source === 'SLACK' ? 'Slack' : '웹' }})</li></ol>
        </div>
      </li>
    </ul>
    <form v-if="preview" class="slack-form" @submit.prevent="send">
      <h3>발송 전 검토</h3>
      <p v-if="!preview.connection.enabled" class="muted">
        Slack 발송이 설정되지 않았거나 꺼져 있습니다. 관리자가 설정 화면에서 연결할 수 있습니다.
      </p>
      <p v-else>받는 채널: <strong>{{ preview.connection.channelLabel }}</strong> (관리자가 등록한 채널 이름)</p>
      <label>보낼 내용
        <textarea v-model="message" rows="10" maxlength="2800" required :disabled="busy" />
      </label>
      <p class="muted">{{ message.length }} / 2,800자 · 승인하면 위 내용이 실제 Slack 채널에 발송됩니다.</p>
      <div class="row">
        <button :disabled="busy || !preview.connection.enabled || !message.trim() || message.length > 2800">
          {{ busy ? '처리 중…' : '승인하고 Slack 발송' }}
        </button>
        <button type="button" class="secondary" :disabled="busy" @click="preview = null">닫기</button>
      </div>
    </form>
  </section>
</template>
<style scoped>.tracking-card { margin-top: 12px; padding: 12px; background: var(--bg); border-radius: 6px; }.tracking-card ol { padding-left: 20px; line-height: 1.8; }</style>

<script setup>
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { api } from '../api.js'
import { slackStatus, canReviewSlack } from '../slack.js'

const props = defineProps({ incidentId: { type: Number, required: true } })
const escalations = ref([])
const deliveries = ref([])
const preview = ref(null)
const message = ref('')
const busy = ref(false)
const error = ref('')
let timer

const delivery = (id) => deliveries.value.find((d) => d.escalationId === id)
async function load() {
  try {
    ;[escalations.value, deliveries.value] = await Promise.all([
      api.incidentEscalations(props.incidentId), api.slackDeliveries(props.incidentId),
    ])
  } catch (e) { error.value = e.message }
}
onMounted(() => { load(); timer = setInterval(() => document.hidden || load(), 5000) })
onUnmounted(() => clearInterval(timer))
watch(() => props.incidentId, () => { preview.value = null; load() })

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
    <h2>Slack 발송</h2>
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

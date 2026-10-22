<script setup>
import { onMounted, ref, watch } from 'vue'
import { api } from '../api.js'

const props = defineProps({ tenants: { type: Array, required: true } })
const tenant = ref('')
const connection = ref(null)
const label = ref('')
const webhook = ref('')
const enabled = ref(false)
const busy = ref(false)
const error = ref('')
const saved = ref(false)

async function load() {
  connection.value = null
  webhook.value = ''
  error.value = ''
  saved.value = false
  if (!tenant.value) return
  busy.value = true
  try {
    connection.value = await api.slackSettings(tenant.value)
    label.value = connection.value.channelLabel
    enabled.value = connection.value.enabled
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}
watch(tenant, load)
watch(() => props.tenants, () => { if (!tenant.value) tenant.value = props.tenants[0]?.id || '' })
onMounted(() => { tenant.value = props.tenants[0]?.id || '' })

async function save() {
  busy.value = true
  error.value = ''
  saved.value = false
  try {
    connection.value = await api.saveSlackSettings(tenant.value, {
      channelLabel: label.value, webhookUrl: webhook.value || null,
      enabled: enabled.value, version: connection.value.version,
    })
    webhook.value = ''
    saved.value = true
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}
</script>

<template>
  <section class="card">
    <h2>Slack 내부 담당자 알림</h2>
    <p>고객사별 운영 채널을 등록하세요. 작업 상세에서 메시지를 검토하고 발송을 승인할 수 있습니다.</p>
    <p class="muted">설정을 저장해도 메시지는 발송되지 않습니다. 이전 호출 기록도 직접 선택해 검토할 수 있습니다.</p>
    <label>고객사
      <select v-model="tenant" :disabled="busy">
        <option v-for="t in tenants" :key="t.id" :value="t.id">{{ t.name }}</option>
      </select>
    </label>
    <p v-if="error" class="error">{{ error }}</p>
    <form v-if="connection" class="slack-form" @submit.prevent="save">
      <label>채널 이름 (관리용)
        <input v-model="label" placeholder="예: #opslens-tenant-a" maxlength="100" required :disabled="busy" />
      </label>
      <label>Incoming Webhook URL
        <input v-model="webhook" type="password" autocomplete="new-password" spellcheck="false"
               :placeholder="connection.configured ? '저장됨 · 변경할 때만 입력' : 'https://hooks.slack.com/services/…'"
               :required="!connection.configured" :disabled="busy" />
      </label>
      <p class="muted">Slack 앱의 Incoming Webhooks에서 운영 채널을 선택해 만든 URL을 입력하세요.
        실제 발송 대상은 URL에 연결된 채널입니다. 채널 이름도 동일하게 적어주세요.</p>
      <label class="toggle"><input v-model="enabled" type="checkbox" :disabled="busy" /> Slack 발송 사용</label>
      <button :disabled="busy">{{ busy ? '저장 중…' : '연결 정보 저장' }}</button>
      <p v-if="saved" role="status">저장했습니다. 작업 상세에서 문안을 검토한 뒤 발송할 수 있습니다.</p>
    </form>
  </section>
</template>

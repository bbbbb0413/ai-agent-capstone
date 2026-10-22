<script setup>
import { ref, watch, onUnmounted } from 'vue'
import { api } from '../api.js'
const props = defineProps({ tenant: { type: String, required: true } })
const emit = defineEmits(['saved'])
const config = ref(null)
const secret = ref('')
const error = ref('')
const saved = ref(false)
const busy = ref(false)
let generation = 0
watch(() => props.tenant, async tenant => {
  const current = ++generation
  config.value = null; secret.value = ''; error.value = ''; saved.value = false
  if (!tenant) return
  busy.value = true
  try { const result = await api.slackTrackingSettings(tenant); if (current === generation) config.value = result }
  catch (e) { if (current === generation) error.value = e.message }
  finally { if (current === generation) busy.value = false }
}, { immediate: true })
onUnmounted(() => ++generation)
async function save() {
  const current = generation
  busy.value = true; error.value = ''; saved.value = false
  try {
    const result = await api.saveSlackTrackingSettings(props.tenant, { ...config.value, signingSecret: secret.value || null })
    if (current !== generation) return
    config.value = result; secret.value = ''; saved.value = true; emit('saved')
  } catch (e) { if (current === generation) error.value = e.message }
  finally { if (current === generation) busy.value = false }
}
</script>
<template>
  <details class="tracking-settings">
    <summary>Slack 버튼과 담당자 연결 설정</summary>
    <p>먼저 위의 발송 연결 정보를 저장하세요. Slack 앱의 Interactivity를 켜고, 공개 HTTPS 주소 뒤에 <code>/api/slack/interactions</code>를 붙인 Request URL을 등록해야 합니다.</p>
    <p>등록된 Slack 사용자만 확인·담당 시작·처리 완료 버튼을 사용할 수 있습니다. 설정 변경은 기존 알림의 Slack 버튼을 무효화하며, 해당 알림은 웹에서 처리할 수 있습니다.</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <form v-if="config" class="slack-form" @submit.prevent="save">
      <label>Slack 앱 ID<input v-model="config.appId" placeholder="A…" required :disabled="busy" /></label>
      <label>Slack 워크스페이스 ID<input v-model="config.teamId" placeholder="T…" required :disabled="busy" /></label>
      <label>Slack 채널 ID<input v-model="config.channelId" placeholder="C… 또는 G…" required :disabled="busy" /></label>
      <label>Slack Signing Secret<input v-model="secret" type="password" autocomplete="new-password" :required="!config.configured"
          :placeholder="config.configured ? '저장됨 · 변경할 때만 입력' : 'Slack 앱 Basic Information에서 확인'" :disabled="busy" /></label>
      <p class="muted">채널 ID는 Webhook에 연결한 채널과 같아야 합니다. Signing Secret은 암호화하여 저장하고 다시 표시하지 않습니다.</p>
      <div v-for="(m, i) in config.members" :key="i" class="row">
        <label>Slack 사용자 ID<input v-model="m.slackUserId" placeholder="U…" required :disabled="busy" /></label>
        <label>OpsLens 담당 계정<select v-model="m.username" required :disabled="busy"><option disabled value="">계정 선택</option><option v-for="u in config.eligibleUsers" :key="u" :value="u">{{ u }}</option></select></label>
        <button type="button" class="secondary" :disabled="busy" @click="config.members.splice(i,1)">연결 삭제</button>
      </div>
      <button type="button" class="secondary" :disabled="busy || config.members.length >= 100" @click="config.members.push({slackUserId:'',username:''})">담당자 연결 추가</button>
      <label class="toggle"><input v-model="config.enabled" type="checkbox" :disabled="busy" /> Slack 처리 버튼 사용</label>
      <button :disabled="busy">처리 추적 설정 저장</button>
      <p v-if="saved" role="status">저장했습니다. 이후 새로 승인하는 알림에 버튼이 포함됩니다.</p>
    </form>
  </details>
</template>
<style scoped>.tracking-settings { margin-top: 24px; border-top: 1px solid var(--border); padding-top: 16px; }.row { align-items: end; }</style>

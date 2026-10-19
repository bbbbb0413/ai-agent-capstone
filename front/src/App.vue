<script setup>
import { onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from './api.js'
import { useSession } from './session.js'
import { useSettings } from './settings.js'

const session = useSession()
const settings = useSettings()
const router = useRouter()
const route = useRoute()

async function signOut() {
  await api.logout().catch(() => {})
  session.signOut()
  router.push('/login')
}

function loadSettings() {
  if (session.isMspStaff) settings.load().catch(() => {})
}

// 다른 탭에서 계정을 바꾸고 돌아오면 표시를 맞춘다
async function syncOnFocus() {
  if (document.visibilityState !== 'visible' || !session.user) return
  await session.sync().catch(() => {})
  if (session.isViewer && !router.currentRoute.value.path.startsWith('/portal')) router.replace('/portal')
  loadSettings()
}
watch(() => route.path, loadSettings, { immediate: true })
onMounted(() => document.addEventListener('visibilitychange', syncOnFocus))
onUnmounted(() => document.removeEventListener('visibilitychange', syncOnFocus))
</script>

<template>
  <header class="topbar">
    <nav class="nav">
      <RouterLink :to="session.isViewer ? '/portal' : '/'" class="brand">OpsLens</RouterLink>
      <template v-if="session.isMspStaff">
        <RouterLink to="/" class="nav-link">운영</RouterLink>
        <RouterLink to="/portal" class="nav-link">고객사 포털 미리보기</RouterLink>
      </template>
    </nav>
    <span v-if="session.user" class="who">
      {{ session.user.username }} · {{ session.user.role }}
      <button class="link" @click="signOut">로그아웃</button>
    </span>
  </header>
  <div v-if="session.isMspStaff && settings.autoApprove" class="banner-warn">
    자동 승인 켜짐: 승인 요청을 사람 확인 없이 바로 승인한다 (테스트용 · {{ settings.updatedBy }}님이 켬)
  </div>
  <main class="page">
    <RouterView />
  </main>
</template>

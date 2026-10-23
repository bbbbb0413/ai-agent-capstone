<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from './api.js'
import AppIcon from './components/AppIcon.vue'
import { clockTime } from './format.js'
import { useSession } from './session.js'
import { useSettings } from './settings.js'

const OPEN_POLL_MS = 30_000
const ROLE_LABELS = { MSP_ADMIN: 'MSP 관리자', MSP_ENGINEER: 'MSP 엔지니어', TENANT_VIEWER: '고객사 담당자' }

const session = useSession()
const settings = useSettings()
const router = useRouter()
const route = useRoute()

const collapsed = ref(localStorage.getItem('opslens.sidebar') === 'collapsed')
const now = ref(new Date())
const openIncidents = ref(0)
let clockTimer
let openTimer

const navItems = computed(() =>
  session.isViewer
    ? [{ to: '/portal', label: '장애 현황', icon: 'building', match: (p) => p.startsWith('/portal') }]
    : [
        { to: '/', label: '운영 개요', icon: 'grid', match: (p) => p === '/' || p.startsWith('/tasks') },
        { to: '/documents', label: '근거 문서', icon: 'file', match: (p) => p.startsWith('/documents') },
        { to: '/portal', label: '고객사 포털 미리보기', icon: 'building', match: (p) => p.startsWith('/portal') },
      ],
)

const title = computed(() => {
  if (route.path === '/portal' && !session.isViewer) return '고객사 포털 미리보기'
  return route.meta.title || 'OpsLens'
})
const clock = computed(() => clockTime(now.value, false))
const incidentsLink = computed(() => (session.isViewer ? '/portal' : { path: '/', hash: '#incidents' }))

function toggleSidebar() {
  collapsed.value = !collapsed.value
  localStorage.setItem('opslens.sidebar', collapsed.value ? 'collapsed' : 'open')
}

async function signOut() {
  await api.logout().catch(() => {})
  session.signOut()
  router.push('/login')
}

function loadSettings() {
  if (session.isMspStaff) settings.load().catch(() => {})
}

async function loadOpenIncidents() {
  if (!session.user || document.hidden) return
  const incidents = await api.incidents().catch(() => null)
  if (incidents) openIncidents.value = incidents.filter((i) => i.status !== 'RESOLVED').length
}

// 다른 탭에서 계정을 바꾸고 돌아오면 표시를 맞춘다
async function syncOnFocus() {
  if (document.visibilityState !== 'visible' || !session.user) return
  await session.sync().catch(() => {})
  if (session.isViewer && !router.currentRoute.value.path.startsWith('/portal')) router.replace('/portal')
  loadSettings()
  loadOpenIncidents()
}
watch(() => route.path, () => {
  loadSettings()
  loadOpenIncidents()
}, { immediate: true })
onMounted(() => {
  document.addEventListener('visibilitychange', syncOnFocus)
  clockTimer = setInterval(() => (now.value = new Date()), 15_000)
  openTimer = setInterval(loadOpenIncidents, OPEN_POLL_MS)
})
onUnmounted(() => {
  document.removeEventListener('visibilitychange', syncOnFocus)
  clearInterval(clockTimer)
  clearInterval(openTimer)
})
</script>

<template>
  <RouterView v-if="!session.user || route.path === '/login'" />

  <div v-else class="shell" :class="{ collapsed }">
    <aside class="sidebar">
      <RouterLink :to="session.isViewer ? '/portal' : '/'" class="side-brand">
        <span class="brand-full">OPSLENS</span><span class="brand-short">OL</span>
      </RouterLink>
      <nav class="side-nav">
        <RouterLink v-for="item in navItems" :key="item.to" :to="item.to"
                    :class="{ active: item.match(route.path) }" :title="item.label">
          <AppIcon :name="item.icon" />
          <span class="side-label">{{ item.label }}</span>
        </RouterLink>
      </nav>
      <div class="side-foot">
        <span class="side-label">AI Ops Console</span>
        <button class="side-toggle" :title="collapsed ? '메뉴 펼치기' : '메뉴 접기'" @click="toggleSidebar">
          <AppIcon :name="collapsed ? 'right' : 'left'" :size="16" />
        </button>
      </div>
    </aside>

    <div class="main">
      <header class="head">
        <div class="head-top">
          <h1 class="head-title">{{ title }}</h1>
          <div class="head-status">
            <span v-if="session.isMspStaff && settings.autoApprove" class="head-pill warn" title="자동 승인 켜짐">
              <AppIcon name="shield" :size="15" /> 자동 승인
            </span>
            <RouterLink :to="incidentsLink" class="head-item" :class="{ alert: openIncidents }" title="처리 중인 인시던트">
              <AppIcon name="bell" :size="16" /> {{ openIncidents }}
            </RouterLink>
            <span class="head-item" :title="ROLE_LABELS[session.user.role]">
              <AppIcon name="user" :size="16" /> {{ session.user.username }}
              <span class="muted role">{{ ROLE_LABELS[session.user.role] }}</span>
            </span>
            <button class="head-item icon-btn" title="로그아웃" @click="signOut"><AppIcon name="logout" :size="16" /></button>
            <span class="head-item clock">{{ clock }}</span>
          </div>
        </div>
        <div id="page-tabs" class="head-tabs"></div>
      </header>
      <div v-if="session.isMspStaff && settings.autoApprove" class="banner-warn">
        자동 승인 켜짐: 승인 요청을 사람 확인 없이 바로 승인한다 (테스트용 · {{ settings.updatedBy }}님이 켬)
      </div>
      <main class="content">
        <!-- 다시 실행한 새 작업처럼 같은 화면의 다른 작업으로 가면 새로 불러온다 -->
        <RouterView :key="route.fullPath" />
      </main>
    </div>
  </div>
</template>

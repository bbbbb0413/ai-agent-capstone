import { createRouter, createWebHistory } from 'vue-router'
import { useSession } from './session.js'
import IncidentsView from './views/IncidentsView.vue'
import LoginView from './views/LoginView.vue'
import PortalIncidentView from './views/PortalIncidentView.vue'
import PortalView from './views/PortalView.vue'
import TaskView from './views/TaskView.vue'

const router = createRouter({
  history: createWebHistory(),
  // 상단 헤더가 고정이라 그 높이만큼 띄워서 멈춘다
  scrollBehavior: (to) => (to.hash ? { el: to.hash, top: 110, behavior: 'smooth' } : { top: 0 }),
  routes: [
    { path: '/login', component: LoginView },
    { path: '/', component: IncidentsView, meta: { title: '운영 개요' } },
    { path: '/tasks/:id', component: TaskView, props: true, meta: { title: '작업 상세' } },
    // 고객사 포털: 고객사 담당자는 여기만 본다. MSP 직원은 고객이 보는 화면을 미리 볼 수 있다
    { path: '/portal', component: PortalView, meta: { title: '장애 현황' } },
    { path: '/portal/incidents/:id', component: PortalIncidentView, props: true, meta: { title: '인시던트 상세' } },
  ],
})

router.beforeEach(async (to) => {
  const session = useSession()
  if (to.path === '/login') return
  if (!session.user) return '/login'
  await session.sync().catch(() => {})
  if (!session.user) return '/login'
  // 운영 화면(작업 진행 과정, 런북, 승인 기록)은 MSP 내부 정보라 고객사 담당자에게 보이지 않는다
  if (session.isViewer && !to.path.startsWith('/portal')) return '/portal'
})

export default router

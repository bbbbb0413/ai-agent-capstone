import { createRouter, createWebHistory } from 'vue-router'
import { useSession } from './session.js'
import IncidentsView from './views/IncidentsView.vue'
import LoginView from './views/LoginView.vue'
import PortalIncidentView from './views/PortalIncidentView.vue'
import PortalView from './views/PortalView.vue'
import TaskView from './views/TaskView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: LoginView },
    { path: '/', component: IncidentsView },
    { path: '/tasks/:id', component: TaskView, props: true },
    // 고객사 포털: 고객사 담당자는 여기만 본다. MSP 직원은 고객이 보는 화면을 미리 볼 수 있다
    { path: '/portal', component: PortalView },
    { path: '/portal/incidents/:id', component: PortalIncidentView, props: true },
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

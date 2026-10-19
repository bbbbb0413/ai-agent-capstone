import { createRouter, createWebHistory } from 'vue-router'
import { useSession } from './session.js'
import LoginView from './views/LoginView.vue'
import IncidentsView from './views/IncidentsView.vue'
import TaskView from './views/TaskView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: LoginView },
    { path: '/', component: IncidentsView },
    { path: '/tasks/:id', component: TaskView, props: true },
  ],
})

router.beforeEach((to) => {
  if (to.path !== '/login' && !useSession().user) return '/login'
})

export default router

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import { setUnauthorizedHandler } from './api.js'
import router from './router.js'
import { useSession } from './session.js'
import './style.css'

const app = createApp(App).use(createPinia()).use(router)

setUnauthorizedHandler(() => {
  useSession().signOut()
  if (router.currentRoute.value.path !== '/login') router.replace('/login')
})

app.mount('#app')

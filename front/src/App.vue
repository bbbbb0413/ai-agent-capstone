<script setup>
import { useRouter } from 'vue-router'
import { useSession } from './session.js'

const session = useSession()
const router = useRouter()

function signOut() {
  session.signOut()
  router.push('/login')
}
</script>

<template>
  <header class="topbar">
    <RouterLink to="/" class="brand">OpsLens</RouterLink>
    <span v-if="session.user" class="who">
      {{ session.user.username }} · {{ session.user.role }}
      <button class="link" @click="signOut">로그아웃</button>
    </span>
  </header>
  <main class="page">
    <RouterView />
  </main>
</template>

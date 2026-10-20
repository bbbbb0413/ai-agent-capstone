<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api.js'
import { useSession } from '../session.js'

const username = ref('engineer')
const password = ref('')
const error = ref('')
const session = useSession()
const router = useRouter()

async function submit() {
  error.value = ''
  try {
    const { username: name, role, tenants } = await api.login(username.value, password.value)
    session.signIn({ username: name, role, tenants })
    router.push('/')
  } catch (e) {
    error.value = e.message
  }
}
</script>

<template>
  <div class="login-screen">
    <form class="card login-card" @submit.prevent="submit">
      <div class="login-brand">OPSLENS</div>
      <p class="muted">AI Ops Console에 로그인한다</p>
      <label>아이디 <input v-model="username" autocomplete="username" /></label>
      <label>비밀번호 <input v-model="password" type="password" autocomplete="current-password" /></label>
      <button type="submit">로그인</button>
      <p v-if="error" class="error">{{ error }}</p>
    </form>
  </div>
</template>

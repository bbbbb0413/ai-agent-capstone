import { defineStore } from 'pinia'
import { api } from './api.js'

// 화면 표시용 로그인 정보. 권한 판단은 backend가 하고, 화면은 버튼을 숨기는 데만 쓴다
export const useSession = defineStore('session', {
  state: () => ({ user: JSON.parse(sessionStorage.getItem('opslens.user') || 'null') }),
  getters: {
    isAdmin: (s) => s.user?.role === 'MSP_ADMIN',
    isMspStaff: (s) => ['MSP_ADMIN', 'MSP_ENGINEER'].includes(s.user?.role),
    isViewer: (s) => s.user?.role === 'TENANT_VIEWER',
  },
  actions: {
    signIn(user) {
      this.user = user
      sessionStorage.setItem('opslens.user', JSON.stringify(user))
    },
    signOut() {
      this.user = null
      sessionStorage.removeItem('opslens.user')
    },
    /**
     * 쿠키가 가리키는 실제 사용자로 표시를 맞춘다. 쿠키는 모든 탭이 함께 쓰므로 다른 탭에서 다른 계정으로
     * 로그인하면 이 탭의 표시와 달라진다. 쿠키가 만료됐으면 api.js의 401 처리가 로그아웃시킨다.
     */
    async sync() {
      if (!this.user) return
      const { username, role, tenants } = await api.me()
      if (username !== this.user.username || role !== this.user.role) this.signIn({ username, role, tenants })
    },
  },
})

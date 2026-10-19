import { defineStore } from 'pinia'

// 화면 표시용 로그인 정보. 권한 판단은 backend가 하고, 화면은 버튼을 숨기는 데만 쓴다
export const useSession = defineStore('session', {
  state: () => ({ user: JSON.parse(sessionStorage.getItem('opslens.user') || 'null') }),
  getters: {
    isMspStaff: (s) => ['MSP_ADMIN', 'MSP_ENGINEER'].includes(s.user?.role),
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
  },
})

import { defineStore } from 'pinia'
import { api } from './api.js'

// backend 운영 설정 (테스트용 자동 승인). 모든 사용자가 같은 값을 본다
export const useSettings = defineStore('settings', {
  state: () => ({ autoApproveAllowed: false, autoApprove: false, updatedBy: null, updatedAt: null }),
  actions: {
    async load() {
      Object.assign(this, await api.settings())
    },
    async setAutoApprove(enabled) {
      Object.assign(this, await api.setAutoApprove(enabled))
    },
  },
})

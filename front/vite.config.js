import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 개발 서버는 /api를 backend로 넘긴다. 배포에서는 nginx가 같은 일을 한다 (nginx.conf)
export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/api': 'http://localhost:8080',
    },
  },
})

import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
// 默认值不变（8756 / 5173）。起第二个实例（如 demo）时用环境变量覆盖，
// 不必改文件：LUMIERE_API_TARGET 指向后端，LUMIERE_DEV_PORT 换前端端口。
const apiTarget = process.env.LUMIERE_API_TARGET || 'http://127.0.0.1:8756'
const devPort = Number(process.env.LUMIERE_DEV_PORT || 5173)

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: devPort,
    strictPort: true,
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
})

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

// 开发时：`npm run dev` 起 5173，/v1 与 /health 代理到本机后端 127.0.0.1:8000。
// 部署时：`npm run deploy` 把构建产物输出到 ../web/vue/，由后端直接服务在 8000 上，
//          与后端同源（认证 Cookie / CSRF 依赖同源，不能跨端口）。
//          访问地址：http://127.0.0.1:8000/（后端把 / 指到 web/vue/index.html）
export default defineConfig({
  // 绝对 base：index.html 被根路径 / 服务，产物本身挂在 /assets/vue/ 下，
  // 相对路径会从 / 解析而错过 /assets/vue/ 前缀，所以必须写绝对前缀。
  base: '/assets/vue/',
  plugins: [vue(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      '/v1': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

// 开发时：`npm run dev` 起 5173，/v1 与 /health 代理到本机后端 127.0.0.1:8000。
// 部署时：`npm run deploy` 把构建产物输出到 ../web/vue/，由后端直接服务在 8000 上，
//          与队友的 web/ 同源（认证 Cookie / CSRF 依赖同源，不能跨端口）。
//          访问地址：http://127.0.0.1:8000/assets/vue/index.html
export default defineConfig({
  // 相对 base：产物被挂在 /assets/vue/ 下时，资源仍能正确解析。
  base: './',
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

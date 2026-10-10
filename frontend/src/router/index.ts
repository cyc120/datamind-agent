import { createRouter, createWebHashHistory } from 'vue-router'

// 用 hash 模式：构建产物由后端挂在 8000 上（/assets/vue/），
// 后端没有 SPA 回退路由，history 模式刷新子路径会 404，hash 模式则不受影响。
const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', name: 'landing', component: () => import('../views/LandingPage.vue') },
    { path: '/app', name: 'app', component: () => import('../views/MainPage.vue') },
  ],
})

export default router

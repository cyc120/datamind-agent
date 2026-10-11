import { createRouter, createWebHashHistory } from 'vue-router'

// 用 hash 模式：构建产物由后端挂在 8000 上（/assets/vue/），
// 后端没有 SPA 回退路由，history 模式刷新子路径会 404，hash 模式则不受影响。
const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', name: 'landing', component: () => import('../views/LandingPage.vue') },
    { path: '/app', name: 'app', component: () => import('../views/MainPage.vue') },
    /**
     * 兜底路由：**没有它就会白屏**。
     * 新前端只实现了 / 与 /app 两个页面，而侧边栏沿用旧版控制台的深链
     * （#/runs、#/model、#/fields、#/authority、#/cad、#/dimensions、#/files），
     * 旧控制台（web/index.html）在 retire legacy UI 那次提交里已经删掉，
     * 这些 hash 变成「匹配不到任何路由」——vue-router 不渲染任何组件，
     * #app 里空掉，用户看到的就是整页白屏（实测 childCount 0、bodyText 空）。
     * 老书签 / 浏览器前进后退同样会命中这里，所以兜底重定向到主界面，
     * 而不是留一个空页面。
     */
    { path: '/:pathMatch(.*)*', name: 'not-found', redirect: '/app' },
  ],
})

export default router

import { createRouter, createWebHashHistory } from 'vue-router'

// 用 hash 模式：构建产物由后端挂在 8000 上（/assets/vue/），
// 后端没有 SPA 回退路由，history 模式刷新子路径会 404，hash 模式则不受影响。
const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', name: 'landing', component: () => import('../views/LandingPage.vue') },
    { path: '/app', name: 'app', component: () => import('../views/MainPage.vue') },
    { path: '/runs', name: 'runs', component: () => import('../views/RunsPage.vue') },

    // 以下六项原先只有侧栏里的旧版 hash 深链（/#model、/#fields …），点进去是白屏。
    // 现在它们都是真正的站内路由：全部 lazy import，保持首屏体积不变。
    { path: '/model', name: 'model', component: () => import('../views/ModelPage.vue') },
    { path: '/fields', name: 'fields', component: () => import('../views/FieldsPage.vue') },
    { path: '/authority', name: 'authority', component: () => import('../views/AuthorityPage.vue') },
    { path: '/cad', name: 'cad', component: () => import('../views/CadPage.vue') },
    { path: '/dimensions', name: 'dimensions', component: () => import('../views/DimensionsPage.vue') },
    { path: '/files', name: 'files', component: () => import('../views/FilesPage.vue') },

    // 兜底必须留在最后：旧版深链（/#runs、/#model、/#fields …）没有前导斜杠，会被
    // 解析成 /runs、/model 这类路径；真正未知的路径也要给出**明确说明**而不是渲染
    // 一个空 <router-view>（那就是白屏，用户只会以为页面坏了）。
    { path: '/:pathMatch(.*)*', name: 'not-migrated', component: () => import('../views/NotMigrated.vue') },
  ],
})

export default router

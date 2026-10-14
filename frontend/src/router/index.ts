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

    // 数据治理：侧栏那 4 条（/fields、/authority、/cad、/dimensions）合并后的唯一入口，
    // 四个板块在页内用分栏切换，当前板块写在 query 上（/governance?tab=cad），
    // 因此刷新与分享链接都能停在同一个分栏。
    //
    // WHY 上面四条旧路由原样保留、不 redirect：它们是深链，可能被书签、docs/ 与测试引用，
    // 「合并入口」不应该以让旧链接失效为代价。旧链接进去仍然渲染原来那一屏（只是它自己的
    // 侧栏已经只剩 1 个「数据治理」入口），与新入口并存。
    { path: '/governance', name: 'governance', component: () => import('../views/GovernancePage.vue') },

    // Wiki 知识图谱（契约 docs/WIKI_API.md §9.1 的路由冻结行）。
    //
    // 两条路由的分工是**有意反转**过并被确认的（WIKI_DOMAIN.md §8 第 8 条）：
    //   `/#/wiki`            → 默认展示**关系图**（当前授权范围图 + 图外可聚焦的词条目录）
    //   `/#/wiki/:entryId`   → 词条深链，**直接展示正文**，并提供返回关系图的入口
    // 版本写在 query 上（`?revision=wrev-…`）。WHY 不放在路径里：`entryId` 是深链的
    // 主体，版本只是它的一个可选项；放在 query 上，刷新与分享链接都能停在同一个词条、
    // 同一个版本（契约 §8.1），而 path 保持"一个词条一个地址"。
    //
    // 顺序要求：这两条必须排在下面的兜底之前，否则 `/#/wiki` 会被 `/:pathMatch(.*)*`
    // 吃掉，用户看到的是「尚未迁移」页（见本文件顶部与 RouterMigrationTests）。
    { path: '/wiki', name: 'wiki', component: () => import('../views/WikiPage.vue') },
    { path: '/wiki/:entryId', name: 'wiki-entry', component: () => import('../views/WikiEntryPage.vue') },

    // 兜底必须留在最后：旧版深链（/#runs、/#model、/#fields …）没有前导斜杠，会被
    // 解析成 /runs、/model 这类路径；真正未知的路径也要给出**明确说明**而不是渲染
    // 一个空 <router-view>（那就是白屏，用户只会以为页面坏了）。
    { path: '/:pathMatch(.*)*', name: 'not-migrated', component: () => import('../views/NotMigrated.vue') },
  ],
})

export default router

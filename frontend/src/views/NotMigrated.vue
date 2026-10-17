<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

/**
 * 未迁移视图的兜底页。
 *
 * 存在的理由很具体：旧版前端的侧边栏是 `/#runs`、`/#model`、`/#fields` 这类 hash 深链，
 * 落地页改版后新前端只定义了 `/` 与 `/app`，**而且没有兜底路由**，于是那 7 个入口
 * 点进去都只渲染出一个空的 `<router-view>` —— 一片白屏。用户看到白屏只会以为
 * 「功能没了/页面坏了」，而真相是「这些视图还没迁移」。
 *
 * 所以这里要说三件事：这条路走不通、为什么、现在能去哪里。
 */

const route = useRoute()

/** 展示用户实际输入的那一段，便于对照文档/旧链接排查。 */
const attempted = computed(() => route.fullPath)

const AVAILABLE = [
  { to: '/app', label: '问数与问答', note: '提问、看证据轨迹' },
  { to: '/runs', label: '运行记录', note: '历史问答与状态' },
]

const NOT_MIGRATED = ['模型接入', '字段复核', '来源权威规则', '图纸与实体', '尺寸核验', '我的文件']
</script>

<template>
  <div class="nm">
    <div class="nm-card">
      <div class="nm-tag">未迁移</div>
      <h1 class="nm-title">这个视图还没有搬到新前端</h1>
      <p class="nm-path">
        你访问的是 <code>{{ attempted }}</code>
      </p>
      <p class="nm-body">
        你看到的不是错误页，而是<strong>确实还没有实现</strong>的页面 —— 旧版前端的这批入口
        （<code>/#model</code>、<code>/#fields</code> 等）在新前端还没有对应的视图，
        所以以前点进来只会得到一片空白。这些功能的后端接口都还在，只是界面待迁移。
      </p>

      <h2 class="nm-h">现在可以用的</h2>
      <ul class="nm-list">
        <li v-for="a in AVAILABLE" :key="a.to">
          <RouterLink class="nm-link" :to="a.to">{{ a.label }}</RouterLink>
          <span class="nm-note">{{ a.note }}</span>
        </li>
      </ul>

      <h2 class="nm-h">尚未迁移</h2>
      <p class="nm-pending">{{ NOT_MIGRATED.join(' · ') }}</p>
    </div>
  </div>
</template>

<style scoped>
/* 类名统一 nm- 前缀，避开 Tailwind @layer utilities 的同名工具类。 */
.nm {
  min-height: 100vh;
  width: 100vw;
  display: grid;
  place-items: center;
  background: var(--color-void);
  padding: 32px;
}
.nm-card {
  max-width: 620px;
  width: 100%;
  padding: 28px 30px 30px;
  border: 1px solid var(--color-line);
  border-radius: 12px;
  background: var(--color-panel);
}
.nm-tag {
  display: inline-block;
  font-size: 12.5px;
  letter-spacing: 0.08em;
  padding: 3px 8px;
  border-radius: 999px;
  color: #d97706;
  border: 1px solid rgba(217, 119, 6, 0.35);
  background: rgba(217, 119, 6, 0.08);
}
/* 页面主标题保留 20px（这是兜底页唯一的大标题），只把颜色转蓝 */
.nm-title {
  margin: 14px 0 8px;
  font-size: 20px;
  color: var(--color-accent);
}
.nm-path {
  margin: 0 0 14px;
  font-size: 14px;
  color: var(--color-text);
}
.nm-body {
  margin: 0 0 22px;
  font-size: 15px;
  line-height: 1.8;
  color: var(--color-body);
}
/* 小标题与其它页统一：蓝色、15px、600（原来是 13.5px/500 灰字，属离群值） */
.nm-h {
  margin: 0 0 8px;
  font-size: 15px;
  letter-spacing: 0.06em;
  color: var(--color-accent);
  font-weight: 600;
}
.nm-list {
  list-style: none;
  margin: 0 0 20px;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.nm-link {
  font-size: 15px;
  color: var(--color-accent);
  text-decoration: none;
  border-bottom: 1px solid rgba(37, 99, 235, 0.3);
}
.nm-link:hover {
  border-bottom-color: var(--color-accent);
}
.nm-note {
  margin-left: 10px;
  font-size: 13.5px;
  color: var(--color-text);
}
.nm-pending {
  margin: 0;
  font-size: 14px;
  color: var(--color-text);
  line-height: 1.8;
}
code {
  font-size: 13.5px;
  padding: 1px 5px;
  border-radius: 5px;
  background: var(--color-ink);
  color: var(--color-text);
}
</style>

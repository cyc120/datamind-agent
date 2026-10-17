<!--
  AnimatedList —— 列表项逐条淡入上浮（staggered reveal）。

  移植自 Vue Bits：https://vue-bits.dev/components/animated-list
  上游源：DavidHDev/vue-bits → src/content/Components/AnimatedList/AnimatedList.vue
  （上游用 motion-v 的 useInView + spring 做逐条入场。）

  为适配本项目改了这些：
  - **实现方式**：上游把每项包进 motion 组件并复制内容。本项目里这些列表是
    "调用方写好的 <li> 文案"，用 slot 复制会重复渲染。改成只对**已有的子元素**
    施加 CSS 动画（scoped 的 :deep(> li)），内容一个字都不用搬 —— 语义、可访问性、
    data-testid 全部保留。
  - 上游是「深色页 + 固定 500px + 自带滚动条 + 每项一张 #222 卡片 + 上下渐隐遮罩」。
    这里要去掉：滚动容器、固定宽度、卡片底色、渐隐遮罩。
  - 上游用 Tailwind 类排版（本仓库出过三次 Tailwind 类名撞名事故：.collapse 让按钮
    永远不可见、.table-caption 让文字一字一行）→ 改 scoped CSS，类名一律 al- 前缀。
  - 上游写死深色（bg-[#222] / text-white / #120F17）→ 继承外层文字颜色。
  - 尊重 prefers-reduced-motion：不做位移，直接显示。

  用法（内容照常写 <li>，不用改成 props）：
    <AnimatedList tag="ul" class="auth-failclosed">
      <li>第一条</li>
      <li>第二条</li>
    </AnimatedList>
-->
<template>
  <component :is="tag" class="al-root"><slot /></component>
</template>

<script setup lang="ts">
withDefaults(
  defineProps<{
    /** 外层用哪个标签渲染（ul / ol） */
    tag?: string
  }>(),
  { tag: 'ul' },
)
</script>

<style scoped>
/* 外层布局（flex column / gap / 左边线）由调用方的类名决定，这里只清默认列表样式。 */
.al-root {
  padding: 0;
  list-style: none;
}

/* 逐条入场：只动 opacity 与 transform，不触发布局重排。
   目标写成「任意直接子元素」而不是只认 li —— 调用方有的地方用 <li>（权威页），
   有的地方用 <p>（图纸页的声明块），两者都要能吃到动画。 */
.al-root :deep(> *) {
  animation: al-rise 0.42s cubic-bezier(0.22, 1, 0.36, 1) both;
  will-change: transform, opacity;
}

/* 前 8 条各错开 70ms；再多就不叠延迟，避免后面的条目等太久才出现。 */
.al-root :deep(> *:nth-child(1)) { animation-delay: 0s; }
.al-root :deep(> *:nth-child(2)) { animation-delay: 0.07s; }
.al-root :deep(> *:nth-child(3)) { animation-delay: 0.14s; }
.al-root :deep(> *:nth-child(4)) { animation-delay: 0.21s; }
.al-root :deep(> *:nth-child(5)) { animation-delay: 0.28s; }
.al-root :deep(> *:nth-child(6)) { animation-delay: 0.35s; }
.al-root :deep(> *:nth-child(7)) { animation-delay: 0.42s; }
.al-root :deep(> *:nth-child(n + 8)) { animation-delay: 0.49s; }

@keyframes al-rise {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

/* 减少动效偏好：直接显示，不做位移。 */
@media (prefers-reduced-motion: reduce) {
  .al-root :deep(> *) {
    animation: none;
    opacity: 1;
    transform: none;
  }
}
</style>

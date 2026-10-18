<!--
  ScrollStackItem —— 卷轴堆栈里的一张卡（ScrollStack 的子组件）。

  移植自 Vue Bits：https://vue-bits.dev/components/scroll-stack
  上游源：DavidHDev/vue-bits → src/content/Components/ScrollStack/ScrollStack.vue
  里的 `ScrollStackItem`（上游用 `h()` 渲染，类名是 Tailwind 的
  `scroll-stack-card relative w-full h-80 my-8 p-12 rounded-[40px] shadow-[...] origin-top`）。

  为适配本项目改了这些：
  1. Tailwind 类名 → scoped CSS（`.ss-card`），尺寸不再写死 `h-80 p-12`：
     高度由内容撑开（最少 `min-height`），内边距与圆角由调用方用
     `:deep(.ss-card)` 覆盖也行 —— 这里给的是一套默认值。
  2. 上游的 `my-8`（上下 margin）与 ScrollStack 里注入的
     `marginBottom = itemDistance` 打架（内联样式赢，但量位置时会差一截），
     这里不设纵向 margin，卡片间距统一由 ScrollStack 负责。
  3. 卡片底色按本页的浅色基调给（白底 + 细描边 + 很轻的投影），
     上游是纯阴影无边框。
-->
<template>
  <div class="ss-card">
    <slot />
  </div>
</template>

<style scoped>
.ss-card {
  position: relative;
  width: 100%;
  min-height: 208px;
  box-sizing: border-box;
  padding: 38px 40px 40px;
  border-radius: 26px;
  background: #ffffff;
  border: 1px solid var(--color-line, rgba(15, 23, 42, 0.1));
  box-shadow:
    0 1px 2px rgba(15, 23, 42, 0.04),
    0 18px 40px -24px rgba(15, 23, 42, 0.28);
  /* ScrollStack 会往里写 transform / will-change，这里只给 3D 上下文 */
  transform-style: preserve-3d;
  backface-visibility: hidden;
}
</style>

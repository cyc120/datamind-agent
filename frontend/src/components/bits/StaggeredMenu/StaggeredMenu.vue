<!--
  StaggeredMenu —— 「错开菜单」条目列表：大号粗体、逐行错开升起、右上角带小号序号。

  移植自 Vue Bits：https://vue-bits.dev/components/staggered-menu
  上游源：DavidHDev/vue-bits → src/content/Components/StaggeredMenu/StaggeredMenu.vue

  上游是一个整屏抽屉菜单：Menu/Close 按钮（`.sm-toggle`，按钮文字做 Menu→Close 轮播）
  + 预铺色层（`.sm-prelayers`）+ 右侧滑出面板（`.staggered-menu-panel`）+ 社交链接
  （`sm-socials`）+ 带序号的条目列表。**这里只移植了「带序号的条目列表」这一块**：
  落地页要的是列表本身的样子（参考图：HOME 01 / ABOUT 02 / PROJECTS 03），不是一个
  从右边滑出来的抽屉 —— 落地页没有二级导航，给它加个 Menu 按钮和全屏面板只会多一层
  没人点的壳。

  为适配本项目改了这些（逐条对照上游）：
  1. 抽屉外壳不移植：`sm-toggle`、`sm-prelayers` / `staggered-menu-panel`、
     `sm-socials`、`logo` 全部去掉。保留：CSS 计数器编号
     （`.sm-panel-list[data-numbering] .sm-panel-item::after` + `decimal-leading-zero`）、
     `.sm-panel-item` 的排版、`.sm-panel-itemLabel` 的 `transform-origin: 50% 100%`、
     以及 gsap 的「yPercent 140 + rotate 10 → 0」逐行错开。
  2. 上游用 Tailwind 工具类 + 内联样式（`text-[4rem] tracking-[-2px] pr-[1.4em]`）。
     本仓库被 Tailwind 类名撞名坑过三次（`.collapse` → 让按钮永远不可见、
     `.table-caption` → 让文字一字一行），改成 scoped CSS，类名统一 `sm-` 前缀，
     与上游同名（便于对照源码）。
  3. 上游条目是 `<a>`（点击跳转）。落地页这四条是陈述、没有链接 → 改成 `<span>`；
     保留 `data-index` 与 `role="list"`。序号仍由 CSS 计数器生成（01–04）。
  4. 上游字号写死 4rem、字色写死 `#000`、悬停写死荧光绿 `#27FF64`。这里字号走
     `fontSize` prop（调用方按页面字号给，默认 2rem），字色继承外层（`color: inherit`），
     强调色（序号 / 悬停）走 `accentColor`。
  5. 上游只在抽屉「打开」时播时间线。这里改成进入视口时播一次（IntersectionObserver），
     并给 `delay` prop，让调用方能把它排在别的动画之后（落地页是翻牌卡翻过来之后）。
  6. 上游序号默认 `opacity: var(--sm-num-opacity, 0)`，靠 gsap 把它推到 1。这里兜底值
     改成 1：脚本没跑 / 视口没触发时序号也在，只是没有入场动画。
  7. 尊重 prefers-reduced-motion：JS 直接不装动画，CSS 里序号常显、标签不做位移。

  依赖：gsap（package.json 里已有 ^3.15.0，与 FoldText / PillNav 同一个库）。

  用法：
    <StaggeredMenu :items="[{ label: 'SQL 全文可见' }, …]" accent-color="#2563eb"
                   font-size="clamp(1.4rem, 2.1vw, 2rem)" :delay="0.35" />
-->
<template>
  <ul
    ref="listRef"
    class="sm-panel-list"
    role="list"
    :data-numbering="numbering || undefined"
    :style="rootStyle"
  >
    <li
      v-for="(item, index) in items"
      :key="`${item.label}-${index}`"
      class="sm-panel-itemWrap"
    >
      <span class="sm-panel-item" :data-index="index + 1">
        <span class="sm-panel-itemLabel">{{ item.label }}</span>
      </span>
    </li>
  </ul>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch, type CSSProperties } from 'vue'
import { gsap } from 'gsap'

interface StaggeredMenuItem {
  label: string
}

const props = withDefaults(
  defineProps<{
    /** 条目文案（按顺序渲染，序号 01… 由 CSS 计数器生成） */
    items?: StaggeredMenuItem[]
    /** 是否显示右上角序号（上游的 displayItemNumbering） */
    numbering?: boolean
    /** 序号与悬停用的强调色（上游 accentColor） */
    accentColor?: string
    /** 条目字号，调用方按页面字号给（上游写死 text-[4rem]） */
    fontSize?: string
    /** 逐行错开的间隔（上游 stagger: { each: 0.1 }） */
    stagger?: number
    /** 单行升起时长（上游 duration: 1） */
    duration?: number
    /** 整段动画延迟，单位秒；用来排在别的动画之后 */
    delay?: number
    /** 什么时候播：进入视口（默认）或挂载即播 */
    trigger?: 'inview' | 'mount'
  }>(),
  {
    items: () => [],
    numbering: true,
    accentColor: '#2563eb',
    fontSize: '2rem',
    stagger: 0.1,
    duration: 1,
    delay: 0,
    trigger: 'inview',
  },
)

const listRef = ref<HTMLElement | null>(null)

const rootStyle = computed<CSSProperties>(
  () =>
    ({
      fontSize: props.fontSize,
      '--sm-accent': props.accentColor,
    }) as CSSProperties,
)

let timeline: gsap.core.Timeline | null = null
let observer: IntersectionObserver | null = null
let played = false

function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && window.matchMedia
    ? window.matchMedia('(prefers-reduced-motion: reduce)').matches
    : false
}

function collect() {
  const root = listRef.value
  if (!root) return { labels: [] as HTMLElement[], numbers: [] as HTMLElement[] }
  return {
    labels: Array.from(root.querySelectorAll<HTMLElement>('.sm-panel-itemLabel')),
    numbers: Array.from(root.querySelectorAll<HTMLElement>('.sm-panel-item')),
  }
}

/** 终态：文字归位、序号不透明。脚本没跑或降级时也用它。 */
function settle() {
  const { labels, numbers } = collect()
  if (!labels.length) return
  gsap.set(labels, { yPercent: 0, rotate: 0, clearProps: 'transform' })
  gsap.set(numbers, { '--sm-num-opacity': 1 })
}

function play() {
  if (played) return
  played = true
  const { labels, numbers } = collect()
  if (!labels.length) return

  // 降级：不动画，直接终态（序号本来就常在，这里只是把可能的 transform 清掉）
  if (prefersReducedMotion()) {
    settle()
    return
  }

  // 上游 buildOpenTimeline 的 item 段：先藏到遮罩下方，再错开升起；
  // 序号晚 0.1s 开始单独淡入（上游 itemsStart + 0.1）。
  gsap.set(labels, { yPercent: 140, rotate: 10 })
  gsap.set(numbers, { '--sm-num-opacity': 0 })

  timeline?.kill()
  timeline = gsap.timeline({ paused: true })
  timeline.to(
    labels,
    {
      yPercent: 0,
      rotate: 0,
      duration: props.duration,
      ease: 'power4.out',
      stagger: { each: props.stagger, from: 'start' },
    },
    props.delay,
  )
  timeline.to(
    numbers,
    {
      duration: 0.6,
      ease: 'power2.out',
      '--sm-num-opacity': 1,
      stagger: { each: 0.08, from: 'start' },
    },
    props.delay + 0.1,
  )
  timeline.play(0)
}

function teardown() {
  observer?.disconnect()
  observer = null
  timeline?.kill()
  timeline = null
  const { labels, numbers } = collect()
  if (labels.length) gsap.killTweensOf(labels)
  if (numbers.length) gsap.killTweensOf(numbers)
}

function setup() {
  teardown()
  played = false
  const root = listRef.value
  if (!root || !props.items.length) return
  if (prefersReducedMotion()) return // CSS 里就是终态

  if (props.trigger === 'mount') {
    play()
    return
  }

  // 进入视口再播：列表初始不藏（脚本失效也看得见），只在播的那一刻设初始态。
  observer = new IntersectionObserver(
    (entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        observer?.disconnect()
        observer = null
        play()
      }
    },
    { threshold: 0.25 },
  )
  observer.observe(root)
}

onMounted(() => {
  setup()
})

onBeforeUnmount(() => {
  teardown()
})

watch(
  () => [props.items, props.trigger, props.stagger, props.duration, props.delay],
  () => setup(),
  { deep: true, flush: 'post' },
)
</script>

<style scoped>
/* 列表本体：上游 sm-panel-list（flex column + gap 0.5rem）。gap 改成 em，跟随字号。 */
.sm-panel-list {
  display: flex;
  flex-direction: column;
  gap: 0.34em;
  margin: 0;
  padding: 0;
  list-style: none;
  text-align: left;
  counter-reset: smItem;
}

/* 每一行是一个遮罩：文字从下方升起时被裁掉，形成上游那种「揭开」感。 */
.sm-panel-itemWrap {
  position: relative;
  overflow: hidden;
  line-height: 1;
  /* 中文没有下伸部，但合并英文时 y/g 会被遮罩切到，留一点余量 */
  padding-bottom: 0.06em;
}

.sm-panel-item {
  position: relative;
  display: inline-flex;
  align-items: flex-start;
  gap: 0.4em;
  /* 上游是 700；用户要求这四行细一点，改成 500（常规偏细，仍够醒目）。 */
  font-weight: 500;
  line-height: 1.04;
  letter-spacing: -0.02em;
  /* 上游写死 uppercase：中文无影响，拉丁字母会全大写（参考图就是大写字） */
  text-transform: uppercase;
  color: inherit;
  text-decoration: none;
  cursor: default;
  transition: color 0.25s ease;
}

.sm-panel-item:hover,
.sm-panel-item:focus-visible {
  color: var(--sm-accent, #2563eb);
}

/* 序号：上游是绝对定位 + counter(smItem, decimal-leading-zero)。
   这里让它做 flex 子项（inline-flex 里 ::after 就是一个 flex item），
   跟着标签右上角走，而不是去猜一个 3.2em 的偏移量。 */
.sm-panel-item::after {
  counter-increment: smItem;
  content: counter(smItem, decimal-leading-zero);
  font-size: 0.32em;
  font-weight: 500;
  line-height: 1;
  letter-spacing: 0.04em;
  color: var(--sm-accent, #2563eb);
  opacity: var(--sm-num-opacity, 1);
  padding-top: 0.16em;
  pointer-events: none;
  user-select: none;
}

.sm-panel-itemLabel {
  display: inline-block;
  will-change: transform;
  transform-origin: 50% 100%;
}

/* 减少动效偏好：不位移、序号常显（JS 那一侧也不再装动画）。 */
@media (prefers-reduced-motion: reduce) {
  .sm-panel-itemLabel {
    transform: none;
    will-change: auto;
  }
  .sm-panel-item::after {
    opacity: 1;
  }
  .sm-panel-item {
    transition: none;
  }
}
</style>

<script setup lang="ts">
/**
 * PillNav —— 「滑动药丸导航」，Vue Bits 官方组件的移植版。
 *
 * 出处：https://vue-bits.dev/components/pill-nav
 * 上游源码（DavidHDev/vue-bits，移植时实际取到的那一份）：
 *   https://github.com/DavidHDev/vue-bits/blob/main/src/content/Components/PillNav/PillNav.vue
 *   目录 src/content/Components/PillNav/ 下【只有 PillNav.vue 一个文件】（456 行），
 *   没有独立 .css —— 所以本移植版同样只有 PillNav.vue，样式全在 <style scoped> 里。
 *
 * 机制（与上游一致）：每个药丸里躺着一颗「弹开圆」。按药丸的宽高算出能包住它的圆的
 * 半径 R / 直径 D / 下移量 delta，把圆设成 scale:0、transformOrigin 落在圆心；悬停时用
 * gsap 时间线在 0.3s 内把圆放大到 1.2 倍盖满药丸，同时把原标签向上顶出、把「悬停标签」
 * 从下方推进来（药丸 overflow:hidden 负责裁切，看起来就是药丸滑过来 / 药丸被填满）。
 * 移开时时间线倒放 0.2s。药丸宽高会变（字体加载、文案变长），所以 window resize、
 * document.fonts.ready、容器尺寸变化都会重新 layout() 重算几何。
 *
 * 与上游的差别（为本项目做的适配，逐条对照）：
 *   1. 上游根元素是【页面头部导航】：absolute 定位 + md: 断点 + 汉堡菜单（<768px 整排药丸
 *      被藏起来）。工具条上的三个动作按钮在任何宽度都必须能点，所以删掉 absolute、汉堡按钮
 *      与移动端抽屉菜单，只保留药丸这一排。上游的 logo 圆钮是「回首页」的 <a>（还会走
 *      RouterLink 分支），本项目这个工具条没有首页可回：logo 降级成可选 prop，传了才渲染，
 *      且渲染成 <span> 而不是链接（不引入 router，也不制造一个点了没反应的假链接）。
 *   2. 上游靠 Tailwind 工具类排版（relative / w-max / md:flex / overflow-hidden / z-[2] …）。
 *      本项目被 Tailwind 工具类重名坑过三次（.collapse、.table-caption），所以这里全部改写成
 *      scoped CSS，新类名统一 pn- 前缀；只有 .pill-label、.pill-label-hover、.is-active 保留
 *      上游原名 —— 前两个是脚本 querySelector 依赖的，且它们都不是 Tailwind 工具类。
 *   3. 上游 item 是 { label, href } 的导航语义，点击交给 RouterLink/浏览器跳转。工具条上这三个
 *      是【动作】不是路由：item 改成 { key, label, testid, tone }，元素改成 <button type="button">，
 *      点击 emit('select', key, item) 回调给父级（键盘 Enter/Space 由 button 原生负责，上游那段
 *      handleKeyDown 是给 <a> 补的，已删）。item.testid 透传到按钮上，测试按 data-testid 取。
 *   4. 上游用 activeHref 标记「当前项」，视觉只是药丸下方一个小圆点（.is-active::after）。这里
 *      ① 语义改成 activeKey；② 激活态直接给实心蓝底白字药丸（与全站刚统一的药丸风格一致），
 *      小圆点在浅色工具条上几乎看不见，去掉。
 *   5. 配色：上游默认值假设深色页面（base #fff / pill #060010 / hover 文字 #060010）。浅色主题下
 *      照搬会白底白字互相吃掉。默认值改成项目令牌：轨道 --color-ink，药丸白底 + --color-line 描边，
 *      悬停/激活 = --color-accent 蓝底 + 白字。又：上游把「弹开圆」的颜色绑死在 --base 上，浅色轨道
 *      会把圆藏起来（#f6f7f9 圆画在 #f6f7f9 轨道上），所以拆出独立的 hoverCircleColor。
 *   6. 上游把标签文字在 DOM 里放了【两份】（可见一份 + 悬停一份），于是按钮的 textContent 是
 *      「导入配置导入配置」。本项目测试按 data-testid 取文本，所以悬停那一份改成
 *      ::after { content: attr(data-label) } 生成 —— DOM 里只有一份文字，动画完全不变。
 *   7. 尺寸不再走内联 :style（内联优先级高，调用处的 CSS 覆盖不了），改成 .pn-root 上的
 *      CSS 变量：调用处（ModelPage 的 .mp-tools）可直接覆盖 --pn-nav-h / --pn-pad-x / --pn-gap /
 *      --pn-font-size。字号取 14px：上游 text-base = 16px，本页控件是 13.5–14px，取上沿，
 *      不再往下调小（用户刚要求全站字号放大一档）。
 *   8. 上游只在 window resize 时重算几何，卸载时也只摘了 resize 监听（gsap 时间线残留）。这里补
 *      ResizeObserver（容器尺寸 / 文案变化）与卸载时 kill 时间线，并在首屏宽度动画结束后清掉
 *      overflow（上游留下的 inline overflow:hidden 会一直压着子元素）。另外 layout() 重排前先
 *      kill 掉「在飞的悬停补间」、重排后若指针仍压在药丸上就把新时间线推到位 —— 否则文案在悬停
 *      状态下变化（正是「点一下导入就换文案」这个场景）会留下鼓着不回去的药丸，或者停在半路。
 *   9. 尊重 prefers-reduced-motion：悬停/移开的过渡时长归零（与 src/style.css 的无障碍策略一致）。
 *
 * 依赖：gsap（package.json 里已有 ^3.15.0，未新增任何依赖）。
 */
import { gsap } from 'gsap'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch, type Ref } from 'vue'

export type PillNavTone = 'default' | 'primary'

export type PillNavItem = {
  /** 稳定标识（上游是 href）：点击时回传给父级，用来决定触发哪个动作 */
  key: string
  label: string
  /** 透传到 <button> 上的 data-testid，本项目的测试依赖它 */
  testid?: string
  /** primary = 实心蓝底白字（主操作）；default = 白底 + 细描边 */
  tone?: PillNavTone
  disabled?: boolean
  title?: string
  /** 覆盖按钮的可访问名；不给就用 label */
  ariaLabel?: string
}

interface PillNavProps {
  items: PillNavItem[]
  /** 上游必填（logo 图片）；本移植版可选：工具条没有 logo，不传就不渲染那颗圆钮 */
  logo?: string
  logoAlt?: string
  /** 当前项（对应上游 activeHref）：高亮成实心蓝药丸 */
  activeKey?: string
  ease?: string
  /** 轨道底色（对应上游 --base） */
  baseColor?: string
  /** 药丸静止底色（对应上游 --pill-bg） */
  pillColor?: string
  /** 药丸静止文字色（对应上游 --pill-text，默认跟 --base） */
  pillTextColor?: string
  /** 悬停/激活时的文字色（对应上游 --hover-text） */
  hoveredPillTextColor?: string
  /** 弹开圆与激活态的颜色（本地新增，见上「与上游的差别」第 5 条） */
  hoverCircleColor?: string
  /** 上游默认 true（首屏 logo 放大 + 药丸排从 0 宽度展开） */
  initialLoadAnimation?: boolean
}

const props = withDefaults(defineProps<PillNavProps>(), {
  logo: '',
  logoAlt: 'Logo',
  activeKey: '',
  ease: 'power3.easeOut',
  baseColor: '#f6f7f9',
  pillColor: '#ffffff',
  pillTextColor: 'var(--color-body, #4b5563)',
  hoveredPillTextColor: '#ffffff',
  hoverCircleColor: 'var(--color-accent, #2563eb)',
  initialLoadAnimation: true,
})

const emit = defineEmits<{
  /** 点击某个药丸（动作按钮）：key 是 item.key */
  select: [key: string, item: PillNavItem]
}>()

const resolvedPillTextColor = computed(() => props.pillTextColor ?? props.baseColor)

const circleRefs = ref<Array<HTMLSpanElement | null>>([])
const tlRefs = ref<Array<gsap.core.Timeline | null>>([])
const activeTweenRefs = ref<Array<gsap.core.Tween | null>>([])
const logoImgRef = ref<HTMLImageElement | null>(null)
const logoTweenRef = ref<gsap.core.Tween | null>(null)
const navItemsRef = ref<HTMLDivElement | null>(null)
const logoRef = ref<HTMLSpanElement | null>(null)

let resizeObserver: ResizeObserver | null = null

const reducedMotion = () =>
  typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia('(prefers-reduced-motion: reduce)').matches
    : false

/** 上游把三个 ref 数组整体重置成 null；文案变化时元素会被复用、ref 不再回调，动画就废了。
 *  这里改成只按长度增减（保留已有元素引用），文案变化只重算几何、不重建引用。 */
const syncRefArrays = (length: number) => {
  const fit = <T,>(source: Ref<Array<T | null>>) => {
    const next = source.value.slice(0, length)
    while (next.length < length) next.push(null)
    source.value = next
  }
  fit(circleRefs)
  fit(tlRefs)
  fit(activeTweenRefs)
}

const layout = () => {
  circleRefs.value.forEach(circle => {
    if (!circle?.parentElement) return

    const pill = circle.parentElement as HTMLElement
    const rect = pill.getBoundingClientRect()
    const { width: w, height: h } = rect
    const R = ((w * w) / 4 + h * h) / (2 * h)
    const D = Math.ceil(2 * R) + 2
    const delta = Math.ceil(R - Math.sqrt(Math.max(0, R * R - (w * w) / 4))) + 1
    const originY = D - delta

    const index = circleRefs.value.indexOf(circle)
    if (index === -1) return

    // 先停掉「在飞的悬停补间」再复位：它下一帧还会继续写 scale，会把下面的 gsap.set(scale:0)
    // 覆盖掉，留下「鼠标已经走了、药丸还鼓着」的残影（上游没有这一步，因为它的重排不含补间）。
    activeTweenRefs.value[index]?.kill()
    activeTweenRefs.value[index] = null

    circle.style.width = `${D}px`
    circle.style.height = `${D}px`
    circle.style.bottom = `-${delta}px`

    gsap.set(circle, {
      xPercent: -50,
      scale: 0,
      transformOrigin: `50% ${originY}px`
    })

    const label = pill.querySelector<HTMLElement>('.pill-label')
    const white = pill.querySelector<HTMLElement>('.pill-label-hover')

    if (label) gsap.set(label, { y: 0 })
    if (white) gsap.set(white, { y: h + 12, opacity: 0 })

    tlRefs.value[index]?.kill()
    const tl = gsap.timeline({ paused: true })

    tl.to(circle, { scale: 1.2, xPercent: -50, duration: 2, ease: props.ease, overwrite: 'auto' }, 0)

    if (label) {
      tl.to(label, { y: -(h + 8), duration: 2, ease: props.ease, overwrite: 'auto' }, 0)
    }

    if (white) {
      gsap.set(white, { y: Math.ceil(h + 100), opacity: 0 })
      tl.to(white, { y: 0, opacity: 1, duration: 2, ease: props.ease, overwrite: 'auto' }, 0)
    }

    tlRefs.value[index] = tl

    // 文案是在「指针还压在药丸上」的时候变的（点一下导入就换文案）：此时不会再有新的
    // mouseenter，不补这一下药丸就停在半路。指针确实还在上面就把新时间线推到位。
    if (pill.matches(':hover')) {
      activeTweenRefs.value[index] = tl.tweenTo(tl.duration(), {
        duration: reducedMotion() ? 0 : 0.3,
        ease: props.ease,
        overwrite: 'auto'
      })
    }
  })
}

const runInitialAnimation = () => {
  if (!props.initialLoadAnimation) return

  const logo = logoRef.value
  const navItems = navItemsRef.value

  if (logo) {
    gsap.set(logo, { scale: 0 })
    gsap.to(logo, { scale: 1, duration: 0.6, ease: props.ease })
  }

  if (navItems) {
    gsap.set(navItems, { width: 0, overflow: 'hidden' })
    gsap.to(navItems, {
      width: 'auto',
      duration: 0.6,
      ease: props.ease,
      // 上游收尾留着 inline 的 overflow:hidden；这里动画结束后摘掉，避免压住子元素。
      onComplete: () => gsap.set(navItems, { clearProps: 'overflow' })
    })
  }
}

const onResize = () => layout()

onMounted(async () => {
  await nextTick()

  layout()

  window.addEventListener('resize', onResize)

  if (document.fonts?.ready) {
    document.fonts.ready.then(layout).catch(() => {})
  }

  // 本地补充：容器尺寸变化（文案变长 / 布局变化）也要重算圆几何。
  if (typeof ResizeObserver !== 'undefined' && navItemsRef.value) {
    resizeObserver = new ResizeObserver(() => layout())
    resizeObserver.observe(navItemsRef.value)
  }

  runInitialAnimation()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  resizeObserver?.disconnect()
  resizeObserver = null
  // 本地补充：上游只摘监听，时间线会残留。
  tlRefs.value.forEach(tl => tl?.kill())
  activeTweenRefs.value.forEach(tween => tween?.kill())
  logoTweenRef.value?.kill()
})

watch(
  () => props.items,
  async items => {
    syncRefArrays(items.length)
    await nextTick()
    layout()
    runInitialAnimation()
  },
  { deep: true, immediate: true }
)

watch(
  () => [props.ease, props.initialLoadAnimation] as const,
  async () => {
    await nextTick()
    layout()
    runInitialAnimation()
  }
)

const isActive = (item: PillNavItem) => !!props.activeKey && item.key === props.activeKey

const handleEnter = (i: number) => {
  const tl = tlRefs.value[i]
  if (!tl) return
  activeTweenRefs.value[i]?.kill()
  activeTweenRefs.value[i] = tl.tweenTo(tl.duration(), {
    duration: reducedMotion() ? 0 : 0.3,
    ease: props.ease,
    overwrite: 'auto'
  })
}

const handleLeave = (i: number) => {
  const tl = tlRefs.value[i]
  if (!tl) return
  activeTweenRefs.value[i]?.kill()
  activeTweenRefs.value[i] = tl.tweenTo(0, {
    duration: reducedMotion() ? 0 : 0.2,
    ease: props.ease,
    overwrite: 'auto'
  })
}

const handleLogoEnter = () => {
  const img = logoImgRef.value
  if (!img) return
  logoTweenRef.value?.kill()
  gsap.set(img, { rotate: 0 })
  logoTweenRef.value = gsap.to(img, {
    rotate: 360,
    duration: 0.2,
    ease: props.ease,
    overwrite: 'auto'
  })
}

const handleSelect = (item: PillNavItem) => {
  if (item.disabled) return
  emit('select', item.key, item)
}

const cssVars = computed(() => ({
  '--base': props.baseColor,
  '--pill-bg': props.pillColor,
  '--hover-text': props.hoveredPillTextColor,
  '--pill-text': resolvedPillTextColor.value,
  '--hover-circle': props.hoverCircleColor
}))

const setCircleRef = (el: HTMLSpanElement | null, index: number) => {
  if (circleRefs.value.length > index) {
    circleRefs.value[index] = el
  }
}
</script>

<template>
  <div class="pn-root">
    <nav class="pn-nav" aria-label="Primary" :style="cssVars">
      <span v-if="logo" ref="logoRef" class="pn-logo" @mouseenter="handleLogoEnter">
        <img ref="logoImgRef" class="pn-logo-img" :src="logo" :alt="logoAlt" />
      </span>

      <div ref="navItemsRef" class="pn-track">
        <ul class="pn-list" role="menubar">
          <li v-for="(item, i) in items" :key="item.key" class="pn-item" role="none">
            <button
              type="button"
              role="menuitem"
              class="pn-pill"
              :class="[`pn-tone-${item.tone || 'default'}`, { 'is-active': isActive(item) }]"
              :data-testid="item.testid"
              :title="item.title"
              :aria-label="item.ariaLabel || item.label"
              :aria-pressed="isActive(item) ? 'true' : undefined"
              :disabled="item.disabled"
              @click="handleSelect(item)"
              @mouseenter="handleEnter(i)"
              @mouseleave="handleLeave(i)"
            >
              <span
                :ref="el => setCircleRef(el as HTMLSpanElement, i)"
                class="pn-hover-circle"
                aria-hidden="true"
              />
              <span class="pn-label-stack">
                <span class="pill-label pn-label">{{ item.label }}</span>
                <!-- 文字由 ::after { content: attr(data-label) } 生成：DOM 里只留一份标签，
                     按钮的 textContent 不会变成「导入配置导入配置」（见头部注释第 6 条）。 -->
                <span class="pill-label-hover pn-label-hover" :data-label="item.label" aria-hidden="true" />
              </span>
            </button>
          </li>
        </ul>
      </div>
    </nav>
  </div>
</template>

<style scoped>
/* 尺寸档与主题色都放在根节点上，调用处可以直接覆盖（上游是内联 :style，覆盖不了）。
   颜色由 props 走内联变量；这里只放尺寸默认值 + 兜底。 */
.pn-root {
  --pn-nav-h: 38px;
  --pn-pad-x: 16px;
  --pn-gap: 4px;
  --pn-font-size: 14px;
  --pn-logo: 36px;
  --pn-ring: var(--color-line, rgba(15, 23, 42, 0.1));
  display: inline-flex;
  box-sizing: border-box;
  max-width: 100%;
}

.pn-nav {
  display: flex;
  align-items: center;
  box-sizing: border-box;
  width: max-content;
  max-width: 100%;
  gap: 8px;
}

/* ── logo 圆钮（可选，工具条不传 logo 时不渲染）── */
.pn-logo {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
  width: var(--pn-logo);
  height: var(--pn-logo);
  padding: 4px;
  border-radius: 999px;
  overflow: hidden;
  background: var(--base, #f6f7f9);
}
.pn-logo-img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

/* ── 药丸轨道 ── */
.pn-track {
  position: relative;
  display: flex;
  align-items: center;
  box-sizing: border-box;
  height: var(--pn-nav-h);
  padding: 3px;
  border: 1px solid var(--pn-ring);
  border-radius: 999px;
  background: var(--base, #f6f7f9);
}

.pn-list {
  display: flex;
  align-items: stretch;
  box-sizing: border-box;
  height: 100%;
  margin: 0;
  padding: 0;
  gap: var(--pn-gap);
  list-style: none;
}

.pn-item {
  display: flex;
  height: 100%;
}

/* ── 药丸本体 ── */
.pn-pill {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
  height: 100%;
  /* 弹开圆与「悬停标签」都靠这颗药丸裁切，这一句是整个效果的支点 */
  overflow: hidden;
  padding: 0 var(--pn-pad-x);
  border: 1px solid var(--pn-ring);
  border-radius: 999px;
  background: var(--pill-bg, #ffffff);
  color: var(--pill-text, #4b5563);
  font: inherit;
  font-size: var(--pn-font-size);
  font-weight: 600;
  line-height: 1;
  letter-spacing: 0.2px;
  white-space: nowrap;
  cursor: pointer;
  transition: color 0.3s ease, border-color 0.2s ease;
}
.pn-pill:hover:not(:disabled),
.pn-pill:focus-visible {
  border-color: transparent;
}
.pn-pill:focus-visible {
  outline: 2px solid var(--color-accent, #2563eb);
  outline-offset: 2px;
}
.pn-pill:disabled {
  opacity: 0.55;
  cursor: default;
}
.pn-pill:active:not(:disabled) {
  transform: scale(0.98);
}

/* 主操作：静止就是蓝底白字（与全站统一后的药丸风格一致） */
.pn-pill.pn-tone-primary {
  border-color: transparent;
  background: var(--hover-circle, #2563eb);
  color: var(--hover-text, #ffffff);
}

/* 激活项（当前项）：同为蓝底白字；上游那个小圆点在浅色工具条上看不见，已去掉 */
.pn-pill.is-active {
  border-color: transparent;
  background: var(--hover-circle, #2563eb);
  color: var(--hover-text, #ffffff);
}

/* 弹开圆：JS 负责宽高 / bottom / transformOrigin / scale，这里只管静态定位与配色。
   主操作与激活项本来就是蓝的，悬停时压深一点，让「药丸被填满」看得出来。 */
.pn-hover-circle {
  position: absolute;
  left: 50%;
  bottom: 0;
  display: block;
  border-radius: 999px;
  background: var(--hover-circle, #2563eb);
  pointer-events: none;
  will-change: transform;
}
.pn-pill.pn-tone-primary .pn-hover-circle,
.pn-pill.is-active .pn-hover-circle {
  background: color-mix(in srgb, var(--hover-circle, #2563eb) 88%, var(--color-text, #0b1220));
}

/* 两份标签叠在同一处：可见的那份往上顶出，悬停那份从下面推进来 */
.pn-label-stack {
  position: relative;
  z-index: 2;
  display: inline-block;
  line-height: 1;
}
.pn-label {
  position: relative;
  z-index: 2;
  display: inline-block;
  line-height: 1;
  will-change: transform;
}
.pn-label-hover {
  position: absolute;
  top: 0;
  left: 0;
  z-index: 3;
  display: inline-block;
  line-height: 1;
  color: var(--hover-text, #ffffff);
  will-change: transform, opacity;
}
.pn-label-hover::after {
  content: attr(data-label);
  display: block;
}

@media (prefers-reduced-motion: reduce) {
  .pn-pill {
    transition: none;
  }
  .pn-pill:active:not(:disabled) {
    transform: none;
  }
}
</style>

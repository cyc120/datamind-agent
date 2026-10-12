import { onBeforeUnmount, onMounted } from 'vue'

/**
 * 给页面里所有 `.reveal` 元素加「进入视口淡入上移」的滚动显现效果。
 *
 * 兜底是必要的，不是保险起见：元素的初始状态就是 `opacity: 0`，一旦
 * IntersectionObserver 没有回调（无头浏览器截图、第三方预览抓取、观察器被策略禁用），
 * 整页内容会永久停在不可见状态——对一个要发链接给别人的落地页来说这是致命的。
 * 因此超过 REVEAL_FALLBACK_MS 仍未触发时，直接全部显示。
 */
const REVEAL_FALLBACK_MS = 1500

/**
 * 显现状态写**行内样式**，不写 class —— 这里踩过坑：
 * 原来是 `el.classList.add('is-visible')`，而「三个关键词」那三张卡是
 * SpecularButton 渲染出来的元素，鼠标悬停要切换 class（加 is-hover）。
 * Vue 重新 patch 整条 class 属性时会把外部加上的 `is-visible` 一起抹掉：
 * 实测悬停第 1 张卡后它的 class 变回 `specular-btn ... feat-card reveal`，
 * 只剩 `.reveal` 的基础规则 `opacity: 0; transform: translateY(28px)`
 * —— 于是卡片在鼠标放上去的一瞬间消失（用户看到的就是「卡片不见了」）。
 * 行内样式优先级同样最高，而且 Vue 不会去动它没有绑定的属性，稳定得多。
 */
function revealNow(el: HTMLElement) {
  el.style.opacity = '1'
  el.style.transform = 'translateY(0)'
}

export function useReveal() {
  let io: IntersectionObserver | null = null
  let fallback: ReturnType<typeof setTimeout> | null = null

  onMounted(() => {
    const targets = Array.from(document.querySelectorAll<HTMLElement>('.reveal'))
    if (!targets.length) return

    const revealAll = () => {
      for (const el of targets) revealNow(el)
    }

    if (typeof IntersectionObserver === 'undefined') {
      revealAll()
      return
    }

    io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) {
            revealNow(e.target as HTMLElement)
            io!.unobserve(e.target)
          }
        }
      },
      { threshold: 0.12 },
    )
    for (const el of targets) io.observe(el)

    fallback = setTimeout(() => {
      revealAll()
      io?.disconnect()
      io = null
    }, REVEAL_FALLBACK_MS)
  })

  onBeforeUnmount(() => {
    if (fallback !== null) clearTimeout(fallback)
    io?.disconnect()
  })
}

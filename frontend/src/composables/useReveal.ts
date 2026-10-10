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

export function useReveal() {
  let io: IntersectionObserver | null = null
  let fallback: ReturnType<typeof setTimeout> | null = null

  onMounted(() => {
    const targets = Array.from(document.querySelectorAll<HTMLElement>('.reveal'))
    if (!targets.length) return

    const revealAll = () => {
      for (const el of targets) el.classList.add('is-visible')
    }

    if (typeof IntersectionObserver === 'undefined') {
      revealAll()
      return
    }

    io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) {
            e.target.classList.add('is-visible')
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

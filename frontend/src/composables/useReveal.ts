import { onMounted, onBeforeUnmount } from 'vue'

/** 给页面里所有 .reveal 元素加「进入视口淡入上移」的滚动显现效果。 */
export function useReveal() {
  let io: IntersectionObserver | null = null

  onMounted(() => {
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
    document.querySelectorAll('.reveal').forEach((el) => io!.observe(el))
  })

  onBeforeUnmount(() => io?.disconnect())
}

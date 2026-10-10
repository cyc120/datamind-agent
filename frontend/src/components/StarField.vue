<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'

const props = withDefaults(
  defineProps<{
    density?: number // 每 10000px² 的星星数量
    maxR?: number
    twinkle?: boolean
    shooting?: boolean
  }>(),
  { density: 1.5, maxR: 1.6, twinkle: true, shooting: true },
)

const canvas = ref<HTMLCanvasElement | null>(null)
let raf = 0
let stars: { x: number; y: number; r: number; a: number; speed: number; hue: number }[] = []
let shoots: { x: number; y: number; ang: number; len: number; life: number }[] = []
let ctx: CanvasRenderingContext2D | null = null

function resize() {
  const el = canvas.value
  if (!el) return
  const dpr = Math.min(window.devicePixelRatio || 1, 2)
  const w = el.clientWidth
  const h = el.clientHeight
  el.width = w * dpr
  el.height = h * dpr
  ctx = el.getContext('2d')
  ctx?.setTransform(dpr, 0, 0, dpr, 0, 0)
  const count = Math.floor(((w * h) / 10000) * props.density)
  stars = Array.from({ length: count }, () => ({
    x: Math.random() * w,
    y: Math.random() * h,
    r: Math.random() * props.maxR + 0.2,
    a: Math.random() * Math.PI * 2,
    speed: 0.4 + Math.random() * 1.4,
    hue: Math.random() < 0.2 ? 210 : Math.random() < 0.5 ? 250 : 190,
  }))
}

function spawnShoot(w: number, h: number) {
  shoots.push({
    x: Math.random() * w * 0.8,
    y: Math.random() * h * 0.4,
    ang: Math.PI / 4 + Math.random() * 0.4,
    len: 60 + Math.random() * 90,
    life: 0,
  })
}

function tick(now: number) {
  const el = canvas.value
  if (!el || !ctx) return
  const w = el.clientWidth
  const h = el.clientHeight
  ctx.clearRect(0, 0, w, h)
  const t = now / 1000
  for (const s of stars) {
    const tw = props.twinkle ? 0.35 + 0.65 * (0.5 + 0.5 * Math.sin(s.a + t * s.speed)) : 0.8
    ctx.beginPath()
    ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2)
    ctx.fillStyle = `hsla(${s.hue}, 90%, 82%, ${tw})`
    ctx.fill()
  }
  if (props.shooting && Math.random() < 0.008 && shoots.length < 2) spawnShoot(w, h)
  shoots = shoots.filter((sh) => {
    sh.life += 1
    const p = sh.life / 60
    const tailX = sh.x - Math.cos(sh.ang) * sh.len
    const tailY = sh.y - Math.sin(sh.ang) * sh.len
    const grad = ctx!.createLinearGradient(sh.x, sh.y, tailX, tailY)
    grad.addColorStop(0, 'rgba(186,230,253,0.9)')
    grad.addColorStop(1, 'rgba(186,230,253,0)')
    ctx!.beginPath()
    ctx!.moveTo(sh.x, sh.y)
    ctx!.lineTo(tailX, tailY)
    ctx!.strokeStyle = grad
    ctx!.lineWidth = 1.5
    ctx!.stroke()
    sh.x += Math.cos(sh.ang) * 7
    sh.y += Math.sin(sh.ang) * 7
    return p < 1
  })
  raf = requestAnimationFrame(tick)
}

onMounted(() => {
  resize()
  window.addEventListener('resize', resize)
  raf = requestAnimationFrame(tick)
})
onBeforeUnmount(() => {
  cancelAnimationFrame(raf)
  window.removeEventListener('resize', resize)
})
</script>

<template>
  <canvas ref="canvas" class="starfield"></canvas>
</template>

<style scoped>
.starfield {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}
</style>

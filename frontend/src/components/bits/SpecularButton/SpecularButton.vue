<script setup lang="ts">
/**
 * SpecularButton —— 「镜面按钮」，Vue Bits 官方组件的移植版。
 * 出处：https://vue-bits.dev/components/specular-button
 *
 * 原理：按钮底下铺一块比按钮大 PAD 的 WebGL 画布，用圆角矩形 SDF 画描边，
 * 再在描边上叠一道「高光条纹」（rim），条纹角度跟着鼠标走 —— 看起来就像
 * 金属/镜面边框把光反到人眼里。按钮本身的底色/边框由 CSS 负责，canvas 只画高光。
 *
 * 与原版的差别：
 *   1. 原版根元素用 tailwind 工具类 + CSS 变量，这里全部改成 scoped CSS
 *      （本项目被 tailwind 工具类重名坑过 .collapse / .table-caption）。
 *   2. 默认配色为浅色主题调过：把高光/描边默认调成中性灰，白卡片上不刺眼。
 *   3. 新增 contentClass / align，方便把「图标+标题+说明」这样的卡片内容排进去
 *      （原版是行内文字按钮，内容只有一行文字 + slot）。
 * 其余（shader、SDF、鼠标跟随、proximity 亮度、销毁逻辑）与原版一致。
 *
 * 依赖：ogl（package.json 里已有 ^1.0.11）。
 */
import { Color, Mesh, Program, Renderer, Triangle } from 'ogl'
import { computed, onMounted, onUnmounted, ref, type CSSProperties } from 'vue'

export type ButtonSize = 'sm' | 'md' | 'lg'

interface SpecularButtonProps {
  size?: ButtonSize
  radius?: number
  tint?: string
  tintOpacity?: number
  blur?: number
  textColor?: string
  lineColor?: string
  baseColor?: string
  intensity?: number
  shineSize?: number
  shineFade?: number
  thickness?: number
  speed?: number
  followMouse?: boolean
  proximity?: number
  autoAnimate?: boolean
  disabled?: boolean
  type?: 'button' | 'submit' | 'reset'
  /** 内容对齐：卡片式用法传 'start' */
  align?: 'center' | 'start'
  /**
   * 渲染成什么元素。默认 button；**纯展示、并没有点击行为时请传 'div'**，
   * 否则会做出一个点了没反应的按钮（本项目刚修过一批「按钮点了没用」）。
   */
  as?: 'button' | 'div'
}

const PAD = 20

// 尺寸档：字号 / 内边距。lg 用于卡片式内容。
const SIZES: Record<ButtonSize, { fontSize: string; padding: string }> = {
  sm: { fontSize: '0.85rem', padding: '10px 22px' },
  md: { fontSize: '1rem', padding: '14px 30px' },
  lg: { fontSize: '1.15rem', padding: '18px 40px' },
}

const VERT = `#version 300 es
in vec2 position;
void main() {
  gl_Position = vec4(position, 0.0, 1.0);
}
`

const FRAG = `#version 300 es
precision highp float;

uniform vec2 uCenter;
uniform vec2 uHalfSize;
uniform float uRadius;
uniform float uAngle;
uniform float uPx;
uniform vec3 uLineColor;
uniform vec3 uBaseColor;
uniform float uIntensity;
uniform float uShineSize;
uniform float uShineFade;
uniform float uThickness;
uniform float uBaseWidth;

out vec4 fragColor;

float sdRoundedRect(vec2 p, vec2 b, float r) {
  vec2 q = abs(p) - b + r;
  return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r;
}

float shapeSDF(vec2 p) { return sdRoundedRect(p, uHalfSize, uRadius); }

float gaussianLine(float d, float sigma) {
  float x = d / (sigma + 1e-6);
  float k = mix(1.0, 1.6, smoothstep(0.0, 1.5, x));
  return exp(-k * x * x);
}

void main() {
  vec2 p = gl_FragCoord.xy - uCenter;
  float d = shapeSDF(p);
  vec2 L = vec2(cos(uAngle), sin(uAngle));

  // 贴着边缘的暗色底描边，给一点厚度感
  float base = (1.0 - smoothstep(0.0, uBaseWidth, abs(d))) * 0.45;

  // 对称高光：朝向/背向光源的两条边都吃到一道亮纹。
  // 用椭圆法线量角度窗口，直边上也能连续变化。
  vec2 nEll = normalize(p / (uHalfSize * uHalfSize) + 1e-6);
  float phi = acos(clamp(abs(dot(nEll, L)), 0.0, 1.0));
  float rim = 1.0 - smoothstep(uShineSize - uShineFade, uShineSize + uShineFade + 1e-4, phi);
  float line = gaussianLine(d, uThickness);
  float edgeClamp = 1.0 - smoothstep(0.5 * uPx, 3.0 * uPx, abs(d));
  float hi = line * rim * edgeClamp * uIntensity;

  vec3 col = uBaseColor * base + uLineColor * hi;
  float a = clamp(base + hi, 0.0, 1.0);
  fragColor = vec4(col, a);
}
`

const props = withDefaults(defineProps<SpecularButtonProps>(), {
  size: 'lg',
  radius: 18,
  tint: '#ffffff',
  tintOpacity: 0.62,
  blur: 8,
  textColor: '#0b1220',
  lineColor: '#334155',
  baseColor: '#64748b',
  intensity: 1,
  shineSize: 12,
  shineFade: 42,
  thickness: 1,
  speed: 0.35,
  followMouse: true,
  proximity: 260,
  autoAnimate: false,
  disabled: false,
  type: 'button',
  align: 'center',
  as: 'button',
})

const btnRef = ref<HTMLElement | null>(null)
const fxRef = ref<HTMLSpanElement | null>(null)

const sizeStyle = computed(() => SIZES[props.size] || SIZES.md)

const buttonStyle = computed<CSSProperties>(
  () =>
    ({
      '--sb-radius': `${props.radius}px`,
      '--sb-tint': props.tint,
      '--sb-tint-opacity': props.tintOpacity,
      '--sb-blur': `${props.blur}px`,
      '--sb-text-color': props.textColor,
    }) as CSSProperties,
)

onMounted(() => {
  const btn = btnRef.value
  const fx = fxRef.value
  if (!btn || !fx) return

  const dpr = window.devicePixelRatio || 1
  const renderer = new Renderer({ alpha: true, premultipliedAlpha: true, antialias: true, dpr })
  const gl = renderer.gl
  gl.clearColor(0, 0, 0, 0)
  gl.enable(gl.BLEND)
  gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA)

  const geometry = new Triangle(gl)
  if (geometry.attributes.uv) delete geometry.attributes.uv

  const program = new Program(gl, {
    vertex: VERT,
    fragment: FRAG,
    uniforms: {
      uCenter: { value: [0, 0] },
      uHalfSize: { value: [1, 1] },
      uRadius: { value: 0 },
      uAngle: { value: 2.4 },
      uPx: { value: dpr },
      uLineColor: { value: [1, 1, 1] },
      uBaseColor: { value: [0.32, 0.32, 0.32] },
      uIntensity: { value: 1 },
      uShineSize: { value: 0.17 },
      uShineFade: { value: 0.7 },
      uThickness: { value: 1 },
      uBaseWidth: { value: dpr },
    },
  })

  const mesh = new Mesh(gl, { geometry, program })
  fx.appendChild(gl.canvas)

  const sizeRef = { w: 1, h: 1 }
  const resize = () => {
    // 小数尺寸 + 显式中心：让 SDF 正好贴在 CSS 边框上，不被 offsetWidth 取整带偏
    const rect = btn.getBoundingClientRect()
    const w = rect.width
    const h = rect.height
    sizeRef.w = w
    sizeRef.h = h
    renderer.setSize(w + PAD * 2, h + PAD * 2)
    program.uniforms.uCenter.value = [(PAD + w / 2) * dpr, (PAD + h / 2) * dpr]
    program.uniforms.uHalfSize.value = [(w / 2) * dpr, (h / 2) * dpr]
  }
  const ro = new ResizeObserver(resize)
  ro.observe(btn)
  resize()

  // 光的方向跟着指针走（全页面范围），指针还没动过时用慢速自转兜底
  let pointerAngle: number | null = null
  let proximityT = 0
  const onPointerMove = (e: PointerEvent) => {
    const rect = btn.getBoundingClientRect()
    const cx = rect.left + rect.width / 2
    const cy = rect.top + rect.height / 2
    const dx = Math.max(rect.left - e.clientX, 0, e.clientX - rect.right)
    const dy = Math.max(rect.top - e.clientY, 0, e.clientY - rect.bottom)
    const dist = Math.hypot(dx, dy)
    if (dist === 0) {
      const nx = (e.clientX - cx) / (rect.width / 2)
      const ny = (cy - e.clientY) / (rect.height / 2)
      pointerAngle = Math.atan2(2 / rect.height, -2 / rect.width) + nx * 0.3 + ny * 0.15
    } else {
      pointerAngle = Math.atan2(cy - e.clientY, e.clientX - cx)
    }
    const t = Math.max(0, 1 - dist / Math.max(props.proximity, 1))
    proximityT = t * t * (3 - 2 * t)
  }
  window.addEventListener('pointermove', onPointerMove)

  let angle = 2.4
  let idleAngle = 2.4
  let bright = 0
  let last = performance.now()
  let raf = 0

  const lineC = new Color()
  const baseC = new Color()

  const update = (now: number) => {
    raf = requestAnimationFrame(update)
    const dt = Math.min((now - last) / 1000, 0.05)
    last = now

    idleAngle += props.speed * dt
    const steer = props.followMouse && pointerAngle != null && (!props.autoAnimate || proximityT > 0)
    const target = steer ? pointerAngle! : idleAngle
    const diff = ((target - angle + Math.PI * 3) % (Math.PI * 2)) - Math.PI
    angle += diff * (1 - Math.exp(-dt * 7))

    const brightTarget = props.autoAnimate ? 1 : proximityT
    bright += (brightTarget - bright) * (1 - Math.exp(-dt * 8))

    lineC.set(props.lineColor)
    baseC.set(props.baseColor)
    program.uniforms.uAngle.value = angle
    program.uniforms.uRadius.value = Math.min(props.radius, Math.min(sizeRef.w, sizeRef.h) / 2) * dpr
    program.uniforms.uLineColor.value = [lineC.r, lineC.g, lineC.b]
    program.uniforms.uBaseColor.value = [baseC.r, baseC.g, baseC.b]
    program.uniforms.uIntensity.value = props.intensity * bright
    program.uniforms.uShineSize.value = (props.shineSize * Math.PI) / 180
    program.uniforms.uShineFade.value = (props.shineFade * Math.PI) / 180
    program.uniforms.uThickness.value = props.thickness * dpr
    renderer.render({ scene: mesh })
  }
  raf = requestAnimationFrame(update)

  onUnmounted(() => {
    cancelAnimationFrame(raf)
    ro.disconnect()
    window.removeEventListener('pointermove', onPointerMove)
    if (gl.canvas.parentNode === fx) fx.removeChild(gl.canvas)
    gl.getExtension('WEBGL_lose_context')?.loseContext()
  })
})
</script>

<template>
  <component
    :is="as"
    ref="btnRef"
    class="specular-btn"
    :class="[`is-${size}`, `align-${align}`, as === 'div' ? 'is-static is-block' : '']"
    :type="as === 'button' ? type : undefined"
    :disabled="as === 'button' ? disabled : undefined"
    :style="buttonStyle"
  >
    <span ref="fxRef" class="sb-fx" aria-hidden="true" />
    <span class="sb-content">
      <slot>Get Started</slot>
    </span>
  </component>
</template>

<style scoped>
.specular-btn {
  position: relative;
  margin: 0;
  /* inline-flex：组件默认用法（行内文字按钮）需要它 */
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: none;
  cursor: pointer;
  font-family: inherit;
  font-weight: 500;
  line-height: 1.4;
  letter-spacing: 0.01em;
  outline: none;
  border-radius: var(--sb-radius, 18px);
  color: var(--sb-text-color, #0b1220);
  /* 玻璃底：tint 透明度 + 背景虚化（玻璃表面） */
  background: color-mix(in srgb, var(--sb-tint, #fff) calc(var(--sb-tint-opacity, 0.62) * 100%), transparent);
  backdrop-filter: blur(var(--sb-blur, 8px)) saturate(1.35);
  -webkit-backdrop-filter: blur(var(--sb-blur, 8px)) saturate(1.35);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.55),
    0 8px 24px rgba(15, 23, 42, 0.08);
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}
/* 块级用法：卡片式内容需要盒子占满一行、内部能正常换行。
   调用方（如落地页的能力卡）用 .feat-card 覆盖成 flex 列即可，
   但不能只写 width:100% —— inline-level 盒子不会被拉伸，内容也就不换行。 */
.specular-btn.is-block {
  display: flex;
  width: 100%;
}
.specular-btn:active {
  transform: scale(0.97);
}
/* 渲染成 div（纯展示卡）时不假装可点：不要按下缩放、鼠标保持默认 */
.specular-btn.is-static {
  cursor: default;
}
.specular-btn.is-static:active {
  transform: none;
}
.specular-btn:disabled {
  cursor: default;
  opacity: 0.55;
}
.specular-btn:disabled:active {
  transform: none;
}
.specular-btn:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 3px;
}
/* 尺寸档 */
.specular-btn.is-sm {
  font-size: 0.85rem;
  padding: 10px 22px;
}
.specular-btn.is-md {
  font-size: 1rem;
  padding: 14px 30px;
}
.specular-btn.is-lg {
  font-size: 1.15rem;
  padding: 18px 40px;
}
.specular-btn.align-start {
  justify-content: flex-start;
  text-align: left;
}
/* 高光层：比按钮大一圈，canvas 只画描边高光，不拦鼠标 */
.sb-fx {
  position: absolute;
  inset: -20px;
  z-index: 1;
  pointer-events: none;
}
.sb-fx :deep(canvas) {
  display: block;
  width: 100%;
  height: 100%;
}
.sb-content {
  position: relative;
  z-index: 2;
  display: block;
  width: 100%;
}
</style>

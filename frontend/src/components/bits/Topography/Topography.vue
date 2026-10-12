<script setup lang="ts">
/**
 * Topography —— 等高线（contour lines）背景，Vue Bits 官方组件的移植版。
 * 出处：https://vue-bits.dev/backgrounds/topography
 *
 * 与原版的差别只有两处，都是为了本项目：
 *   1. 原版根元素用 tailwind 工具类（relative / w-full / h-full / overflow-hidden），
 *      这里换成 scoped 的 .topography —— 本项目被 tailwind 工具类坑过两次
 *      （.collapse、.table-caption 重名），不引入新的工具类依赖。
 *   2. 默认配色改成浅色主题（原版默认是紫→粉→白的深色霓虹），
 *      并且默认降低透明度/发光，避免白底上过曝。
 * 其余（shader、uniform、鼠标交互、可见性节流、销毁逻辑）与原版一致。
 *
 * 依赖：ogl（package.json 里已有 ^1.0.11）。
 */
import { Mesh, Program, Renderer, Triangle } from 'ogl'
import { onMounted, onUnmounted, ref, watch } from 'vue'

export type ColorMode = 'elevation' | 'uniform' | 'alternating'

interface TopographyProps {
  lowColor?: string
  midColor?: string
  highColor?: string
  speed?: number
  morphAmount?: number
  morphSpeed?: number
  bands?: number
  thickness?: number
  scale?: number
  pixelSize?: number
  glow?: number
  colorMode?: ColorMode
  contrast?: number
  brightness?: number
  fillBands?: boolean
  opacity?: number
  grain?: boolean
  grainIntensity?: number
  mouseInteraction?: boolean
  mouseRadius?: number
  mouseStrength?: number
}

const hexToRgb = (hex: string): [number, number, number] => {
  const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex)
  if (!result) return [1, 1, 1]
  return [parseInt(result[1], 16) / 255, parseInt(result[2], 16) / 255, parseInt(result[3], 16) / 255]
}

const colorModeToFloat = (mode: ColorMode): number => {
  if (mode === 'uniform') return 1.0
  if (mode === 'alternating') return 2.0
  return 0.0
}

const vertex = `#version 300 es
in vec2 position;
void main() {
  gl_Position = vec4(position, 0.0, 1.0);
}
`

const fragment = `#version 300 es
precision highp float;
uniform vec2 iResolution;
uniform float iTime;
uniform float uMorphAmount;
uniform float uBands;
uniform float uThickness;
uniform float uScale;
uniform float uPixelSize;
uniform float uGlow;
uniform float uColorMode;
uniform float uContrast;
uniform float uBrightness;
uniform float uFillBands;
uniform float uOpacity;
uniform vec3 uLow;
uniform vec3 uMid;
uniform vec3 uHigh;
uniform vec2 uMouse;
uniform float uMouseEnabled;
uniform float uMouseRadius;
uniform float uMouseStrength;
uniform float uMouseActive;
uniform float uGrain;
uniform float uGrainIntensity;
uniform vec4 uCtrlA;
uniform vec4 uCtrlB;
uniform vec4 uCtrlC;
uniform vec4 uCtrlD;
out vec4 fragColor;

float bez(float t, vec4 c) {
  float w = 6.2831853 * t;
  return 0.5 * (c.x * sin(w) + c.y * cos(w) + c.z * sin(2.0 * w) + c.w * cos(2.0 * w));
}

float field(vec2 uv) {
  vec2 a = vec2(bez(uv.x, uCtrlA), bez(uv.x, uCtrlB));
  vec2 b = vec2(bez(uv.y, uCtrlC), bez(uv.y, uCtrlD));
  return distance(a, b);
}

vec3 elevationColor(float e) {
  vec3 c = mix(uLow, uMid, smoothstep(0.0, 0.5, e));
  c = mix(c, uHigh, smoothstep(0.5, 1.0, e));
  return c;
}

void main() {
  vec2 res = iResolution.xy;
  vec2 uv = gl_FragCoord.xy / res;

  vec2 suv = (uv - 0.5) / max(uScale, 0.001) + 0.5;

  vec2 sampleUv = suv;
  if (uPixelSize > 1.0) {
    vec2 px = res / uPixelSize;
    sampleUv = (floor(suv * px) + 0.5) / px;
  }

  float fv = field(sampleUv);

  if (uMouseEnabled > 0.5) {
    vec2 d = uv - uMouse;
    d.x *= res.x / max(res.y, 1.0);
    float r = max(uMouseRadius, 0.001);
    float bump = exp(-dot(d, d) / (r * r)) * uMouseStrength * uMouseActive;
    fv += bump;
  }

  float f = fv * uBands;
  float frac = fract(f);
  float lineDist = min(frac, 1.0 - frac);

  float aa = fwidth(f) + 0.0001;
  float mask = 1.0 - smoothstep(uThickness - aa, uThickness + aa, lineDist);

  float glowR = uThickness + uGlow * 0.5 + aa;
  float glow = (1.0 - smoothstep(uThickness, glowR, lineDist)) * step(0.0001, uGlow);

  float elev = clamp(fv / (uMorphAmount * 2.5 + 0.001), 0.0, 1.0);

  vec3 lineCol;
  if (uColorMode < 0.5) {
    lineCol = elevationColor(elev);
  } else if (uColorMode < 1.5) {
    lineCol = uMid;
  } else {
    float parity = mod(floor(f), 2.0);
    lineCol = mix(uMid, uHigh, parity);
  }

  float coverage = clamp(mask + glow * 0.55, 0.0, 1.0);
  coverage = pow(coverage, max(uContrast, 0.001));

  vec3 outColor = lineCol;
  float outAlpha = coverage;

  if (uFillBands > 0.5) {
    vec3 fillCol = elevationColor(elev);
    float fillA = 0.1 * elev;
    outColor = mix(fillCol, lineCol, coverage);
    outAlpha = clamp(coverage + fillA, 0.0, 1.0);
  }

  if (uGrain > 0.5) {
    float g = fract(sin(dot(gl_FragCoord.xy, vec2(12.9898, 78.233)) + iTime) * 43758.5453);
    outAlpha += (g - 0.5) * uGrainIntensity;
  }

  outColor *= uBrightness;
  outColor = clamp(outColor, 0.0, 1.0);

  float a = clamp(outAlpha, 0.0, 1.0) * uOpacity;
  fragColor = vec4(outColor * a, a);
}
`

const CTRL_INDICES = [
  [1, -2, 3, -4],
  [9, -8, 7, -6],
  [5, 2, 5, -5],
  [-1, -3, 8, 9],
]

interface ScalarUniform {
  value: number
}
interface Vec2Uniform {
  value: Float32Array
}
interface Vec3Uniform {
  value: Float32Array
}
interface Vec4Uniform {
  value: Float32Array
}

interface MainUniforms {
  iTime: ScalarUniform
  iResolution: Vec2Uniform
  uSpeed: ScalarUniform
  uMorphAmount: ScalarUniform
  uMorphSpeed: ScalarUniform
  uBands: ScalarUniform
  uThickness: ScalarUniform
  uScale: ScalarUniform
  uPixelSize: ScalarUniform
  uGlow: ScalarUniform
  uColorMode: ScalarUniform
  uContrast: ScalarUniform
  uBrightness: ScalarUniform
  uFillBands: ScalarUniform
  uOpacity: ScalarUniform
  uGrain: ScalarUniform
  uGrainIntensity: ScalarUniform
  uLow: Vec3Uniform
  uMid: Vec3Uniform
  uHigh: Vec3Uniform
  uMouse: Vec2Uniform
  uMouseEnabled: ScalarUniform
  uMouseRadius: ScalarUniform
  uMouseStrength: ScalarUniform
  uMouseActive: ScalarUniform
  uCtrlA: Vec4Uniform
  uCtrlB: Vec4Uniform
  uCtrlC: Vec4Uniform
  uCtrlD: Vec4Uniform
}

// 默认值是浅色主题：白底上要的是「淡等高线」，不是原版的深色霓虹。
const props = withDefaults(defineProps<TopographyProps>(), {
  lowColor: '#94a3b8',
  midColor: '#64748b',
  highColor: '#cbd5e1',
  speed: 0.35,
  morphAmount: 3.0,
  morphSpeed: 0.05,
  bands: 2.0,
  thickness: 0.01,
  scale: 1.0,
  pixelSize: 1.0,
  glow: 0.25,
  colorMode: 'elevation',
  contrast: 3.0,
  brightness: 1.0,
  fillBands: false,
  opacity: 0.55,
  grain: false,
  grainIntensity: 0.05,
  mouseInteraction: true,
  mouseRadius: 0.3,
  mouseStrength: 0.4,
})

const containerRef = ref<HTMLDivElement | null>(null)

let program: MainUniforms | null = null
let teardown: (() => void) | null = null

const syncUniforms = () => {
  if (!program) return
  const u = program

  u.uSpeed.value = props.speed
  u.uMorphAmount.value = props.morphAmount
  u.uMorphSpeed.value = props.morphSpeed
  u.uBands.value = props.bands
  u.uThickness.value = props.thickness
  u.uScale.value = props.scale
  u.uPixelSize.value = props.pixelSize
  u.uGlow.value = props.glow
  u.uColorMode.value = colorModeToFloat(props.colorMode)
  u.uContrast.value = props.contrast
  u.uBrightness.value = props.brightness
  u.uFillBands.value = props.fillBands ? 1.0 : 0.0
  u.uOpacity.value = props.opacity
  u.uGrain.value = props.grain ? 1.0 : 0.0
  u.uGrainIntensity.value = props.grainIntensity
  u.uLow.value = new Float32Array(hexToRgb(props.lowColor))
  u.uMid.value = new Float32Array(hexToRgb(props.midColor))
  u.uHigh.value = new Float32Array(hexToRgb(props.highColor))
  u.uMouseEnabled.value = props.mouseInteraction ? 1.0 : 0.0
  u.uMouseRadius.value = props.mouseRadius
  u.uMouseStrength.value = props.mouseStrength
}

watch(
  () => [
    props.lowColor,
    props.midColor,
    props.highColor,
    props.speed,
    props.morphAmount,
    props.morphSpeed,
    props.bands,
    props.thickness,
    props.scale,
    props.pixelSize,
    props.glow,
    props.colorMode,
    props.contrast,
    props.brightness,
    props.fillBands,
    props.opacity,
    props.grain,
    props.grainIntensity,
    props.mouseInteraction,
    props.mouseRadius,
    props.mouseStrength,
  ],
  () => syncUniforms(),
)

onMounted(() => {
  const container = containerRef.value
  if (!container) return

  const renderer = new Renderer({
    webgl: 2,
    alpha: true,
    premultipliedAlpha: true,
    antialias: false,
    // preserveDrawingBuffer 让合成器之外也能读回像素（截图/自检时 toDataURL 才有内容）
    preserveDrawingBuffer: true,
    dpr: Math.min(window.devicePixelRatio || 1, 2),
  })

  const gl = renderer.gl
  gl.clearColor(0, 0, 0, 0)
  const canvas = gl.canvas as HTMLCanvasElement
  canvas.style.width = '100%'
  canvas.style.height = '100%'
  canvas.style.display = 'block'
  container.appendChild(canvas)

  const geometry = new Triangle(gl)
  const localProgram = new Program(gl, {
    vertex,
    fragment,
    uniforms: {
      iTime: { value: 0 },
      iResolution: { value: new Float32Array([1, 1]) },
      uSpeed: { value: 0.35 },
      uMorphAmount: { value: 3.0 },
      uMorphSpeed: { value: 0.05 },
      uBands: { value: 2.0 },
      uThickness: { value: 0.01 },
      uScale: { value: 1.0 },
      uPixelSize: { value: 1.0 },
      uGlow: { value: 0.5 },
      uColorMode: { value: 0.0 },
      uContrast: { value: 3.0 },
      uBrightness: { value: 1.0 },
      uFillBands: { value: 0.0 },
      uOpacity: { value: 1.0 },
      uGrain: { value: 1.0 },
      uGrainIntensity: { value: 0.05 },
      uLow: { value: new Float32Array([1, 1, 1]) },
      uMid: { value: new Float32Array([1, 1, 1]) },
      uHigh: { value: new Float32Array([1, 1, 1]) },
      uMouse: { value: new Float32Array([0.5, 0.5]) },
      uMouseEnabled: { value: 1.0 },
      uMouseRadius: { value: 0.3 },
      uMouseStrength: { value: 0.4 },
      uMouseActive: { value: 0.0 },
      uCtrlA: { value: new Float32Array([0, 0, 0, 0]) },
      uCtrlB: { value: new Float32Array([0, 0, 0, 0]) },
      uCtrlC: { value: new Float32Array([0, 0, 0, 0]) },
      uCtrlD: { value: new Float32Array([0, 0, 0, 0]) },
    },
  })
  const mu = localProgram.uniforms as unknown as MainUniforms
  program = mu

  const mesh = new Mesh(gl, { geometry, program: localProgram })

  const setSize = () => {
    const rect = container.getBoundingClientRect()
    const w = Math.max(1, Math.floor(rect.width))
    const h = Math.max(1, Math.floor(rect.height))
    renderer.setSize(w, h)
    const res = mu.iResolution.value
    res[0] = gl.drawingBufferWidth
    res[1] = gl.drawingBufferHeight
    renderer.render({ scene: mesh })
  }

  const ro = new ResizeObserver(setSize)
  ro.observe(container)
  setSize()

  const currentMouse: [number, number] = [0.5, 0.5]
  const targetMouse: [number, number] = [0.5, 0.5]
  let mouseActive = 0
  let mouseActiveTarget = 0

  // 监听挂在容器上（原版挂在 canvas 上）：本项目里 canvas 是 pointer-events:none 的装饰层
  const onMouseMove = (e: MouseEvent) => {
    const rect = container.getBoundingClientRect()
    targetMouse[0] = (e.clientX - rect.left) / Math.max(1, rect.width)
    targetMouse[1] = 1.0 - (e.clientY - rect.top) / Math.max(1, rect.height)
    mouseActiveTarget = 1
  }
  const onMouseLeave = () => {
    mouseActiveTarget = 0
  }
  container.addEventListener('mousemove', onMouseMove)
  container.addEventListener('mouseleave', onMouseLeave)

  const ctrlArrays = [mu.uCtrlA.value, mu.uCtrlB.value, mu.uCtrlC.value, mu.uCtrlD.value]

  let raf = 0
  let isVisible = true
  let isPageVisible = !document.hidden
  const t0 = performance.now()

  const loop = (t: number) => {
    const time = (t - t0) * 0.001
    mu.iTime.value = time

    const ma = mu.uMorphAmount.value
    const sp = mu.uSpeed.value
    const msp = mu.uMorphSpeed.value
    for (let g = 0; g < 4; g++) {
      const arr = ctrlArrays[g]
      const idx = CTRL_INDICES[g]
      for (let j = 0; j < 4; j++) {
        const i = idx[j]
        arr[j] = ma * Math.sin(time * sp * Math.sin(i * msp) + i)
      }
    }

    currentMouse[0] += 0.05 * (targetMouse[0] - currentMouse[0])
    currentMouse[1] += 0.05 * (targetMouse[1] - currentMouse[1])
    const m = mu.uMouse.value
    m[0] = currentMouse[0]
    m[1] = currentMouse[1]

    mouseActive += 0.05 * (mouseActiveTarget - mouseActive)
    mu.uMouseActive.value = mouseActive

    renderer.render({ scene: mesh })
    raf = requestAnimationFrame(loop)
  }

  const tryStart = () => {
    if (isVisible && isPageVisible && raf === 0) raf = requestAnimationFrame(loop)
  }
  const tryStop = () => {
    if (raf !== 0) {
      cancelAnimationFrame(raf)
      raf = 0
    }
  }

  const io = new IntersectionObserver(
    ([entry]) => {
      isVisible = entry.isIntersecting
      if (isVisible) tryStart()
      else tryStop()
    },
    { threshold: 0 },
  )
  io.observe(container)

  const onVisibility = () => {
    isPageVisible = !document.hidden
    if (isPageVisible) tryStart()
    else tryStop()
  }
  document.addEventListener('visibilitychange', onVisibility)

  tryStart()
  syncUniforms()

  teardown = () => {
    tryStop()
    ro.disconnect()
    io.disconnect()
    document.removeEventListener('visibilitychange', onVisibility)
    container.removeEventListener('mousemove', onMouseMove)
    container.removeEventListener('mouseleave', onMouseLeave)
    program = null
    try {
      container.removeChild(canvas)
    } catch {
      /* canvas 已脱离，忽略 */
    }
    gl.getExtension('WEBGL_lose_context')?.loseContext()
  }
})

onUnmounted(() => {
  teardown?.()
  teardown = null
})
</script>

<template>
  <div ref="containerRef" class="topography" aria-hidden="true" />
</template>

<style scoped>
.topography {
  position: relative;
  width: 100%;
  height: 100%;
  overflow: hidden;
}
</style>

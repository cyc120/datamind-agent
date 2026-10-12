<script setup lang="ts">
/**
 * GlassSurface —— 「液态玻璃」表面，Vue Bits 官方组件的移植版。
 * 出处：https://vue-bits.dev/components/glass-surface
 *
 * 原理：用一张动态生成的 SVG 位移贴图（边缘渐变的红/蓝通道 + mix-blend-mode）
 * 做 feDisplacementMap，再叠三通道 + 高斯模糊，得到「玻璃边缘把背景折射扭曲」
 * 的效果；浏览器不支持 SVG 滤镜（Safari / Firefox）时退化为 backdrop-filter 玻璃。
 *
 * 与原版的差别：
 *   1. tailwind 工具类 → scoped CSS（本项目被工具类重名坑过 .collapse / .table-caption）；
 *   2. 默认值改成浅色主题：原版按 prefers-color-scheme 切深/浅两套，
 *      这里本项目只做浅色，直接固定浅色那一套，去掉深色分支与媒体查询监听；
 *   3. 暴露 slot 之外还保留原版的居中容器（.glass-inner）。
 *
 * 依赖：无（纯 SVG + CSS）。
 */
import { computed, nextTick, onMounted, onUnmounted, ref, useTemplateRef, watch } from 'vue'

interface GlassSurfaceProps {
  width?: string | number
  height?: string | number
  borderRadius?: number
  borderWidth?: number
  brightness?: number
  opacity?: number
  blur?: number
  displace?: number
  backgroundOpacity?: number
  saturation?: number
  distortionScale?: number
  redOffset?: number
  greenOffset?: number
  blueOffset?: number
  xChannel?: 'R' | 'G' | 'B'
  yChannel?: 'R' | 'G' | 'B'
  mixBlendMode?: string
  /** 背景模糊量（px）。原版靠 SVG 滤镜，没有这一项；浅色低位移下必须补模糊才有玻璃感 */
  glassBlur?: number
  /** 内容内边距：卡片式用法直接由调用方控制 */
  innerPadding?: string
}

const props = withDefaults(defineProps<GlassSurfaceProps>(), {
  width: '100%',
  height: '100%',
  borderRadius: 20,
  borderWidth: 0.07,
  brightness: 78,
  opacity: 0.93,
  blur: 11,
  displace: 0.6,
  backgroundOpacity: 0.16,
  saturation: 1.5,
  distortionScale: -180,
  redOffset: 0,
  greenOffset: 10,
  blueOffset: 20,
  xChannel: 'R',
  yChannel: 'G',
  mixBlendMode: 'difference',
  glassBlur: 14,
  innerPadding: '0px',
})

// 同一个页面可能挂多个实例，SVG filter / 渐变 id 必须唯一
let seq = 0
const uniqueId = `glass-${Date.now().toString(36)}-${seq++}`
const filterId = `glass-filter-${uniqueId}`
const redGradId = `red-grad-${uniqueId}`
const blueGradId = `blue-grad-${uniqueId}`

const containerRef = useTemplateRef<HTMLDivElement>('containerRef')
const feImageRef = useTemplateRef<SVGFEImageElement>('feImageRef')
const redChannelRef = useTemplateRef<SVGFEDisplacementMapElement>('redChannelRef')
const greenChannelRef = useTemplateRef<SVGFEDisplacementMapElement>('greenChannelRef')
const blueChannelRef = useTemplateRef<SVGFEDisplacementMapElement>('blueChannelRef')
const gaussianBlurRef = useTemplateRef<SVGFEGaussianBlurElement>('gaussianBlurRef')

let resizeObserver: ResizeObserver | null = null

const generateDisplacementMap = () => {
  const rect = containerRef.value?.getBoundingClientRect()
  const actualWidth = rect?.width || 400
  const actualHeight = rect?.height || 200
  const edgeSize = Math.min(actualWidth, actualHeight) * (props.borderWidth * 0.5)

  const svgContent = `
      <svg viewBox="0 0 ${actualWidth} ${actualHeight}" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <linearGradient id="${redGradId}" x1="100%" y1="0%" x2="0%" y2="0%">
            <stop offset="0%" stop-color="#0000"/>
            <stop offset="100%" stop-color="red"/>
          </linearGradient>
          <linearGradient id="${blueGradId}" x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" stop-color="#0000"/>
            <stop offset="100%" stop-color="blue"/>
          </linearGradient>
        </defs>
        <rect x="0" y="0" width="${actualWidth}" height="${actualHeight}" fill="black"></rect>
        <rect x="0" y="0" width="${actualWidth}" height="${actualHeight}" rx="${props.borderRadius}" fill="url(#${redGradId})" />
        <rect x="0" y="0" width="${actualWidth}" height="${actualHeight}" rx="${props.borderRadius}" fill="url(#${blueGradId})" style="mix-blend-mode: ${props.mixBlendMode}" />
        <rect x="${edgeSize}" y="${edgeSize}" width="${actualWidth - edgeSize * 2}" height="${actualHeight - edgeSize * 2}" rx="${props.borderRadius}" fill="hsl(0 0% ${props.brightness}% / ${props.opacity})" style="filter:blur(${props.blur}px)" />
      </svg>
    `

  return `data:image/svg+xml,${encodeURIComponent(svgContent)}`
}

const updateDisplacementMap = () => {
  if (feImageRef.value) {
    feImageRef.value.setAttribute('href', generateDisplacementMap())
  }
}

const supportsSVGFilters = () => {
  if (typeof window === 'undefined' || typeof navigator === 'undefined') return false
  const isWebkit = /Safari/.test(navigator.userAgent) && !/Chrome/.test(navigator.userAgent)
  const isFirefox = /Firefox/.test(navigator.userAgent)
  if (isWebkit || isFirefox) return false
  const div = document.createElement('div')
  div.style.backdropFilter = `url(#${filterId})`
  return div.style.backdropFilter !== ''
}

const supportsBackdropFilter = () =>
  typeof window !== 'undefined' && CSS.supports('backdrop-filter', 'blur(10px)')

const containerStyles = computed(() => {
  const base: Record<string, string | number> = {
    width: typeof props.width === 'number' ? `${props.width}px` : props.width,
    height: typeof props.height === 'number' ? `${props.height}px` : props.height,
    borderRadius: `${props.borderRadius}px`,
    '--glass-tint': `hsl(0 0% 100% / ${props.backgroundOpacity})`,
    '--glass-saturation': props.saturation,
  }
  if (supportsSVGFilters()) {
    return {
      ...base,
      background: `hsl(0 0% 100% / ${props.backgroundOpacity})`,
      /**
       * ⚠ 这里在原版基础上加了 blur()：backdrop-filter 一旦写成 `url(#f) saturate()`，
       * 整条声明就被 SVG 滤镜接管，**背景不再被模糊**——只靠 SVG 位移只能把边缘
       * 扭曲出一点彩色纹路，看起来完全不像玻璃（用户反馈「玻璃材质不明显」）。
       * 原版的 URL 滤镜位移量很大（-180~-200），那种强度下边缘撕裂本身就很抢眼，
       * 所以不叠 blur 也还行；本项目收到 -55 之后必须补上模糊，否则玻璃没有体积感。
       */
      backdropFilter: `url(#${filterId}) blur(${props.glassBlur}px) saturate(${props.saturation})`,
      WebkitBackdropFilter: `url(#${filterId}) blur(${props.glassBlur}px) saturate(${props.saturation})`,
      /**
       * 浅色主题的玻璃厚度：外圈 1px 亮边 + 内圈 1px 暗边 + 顶部内高光。
       * 原版只有很淡的内阴影，白底上几乎看不出「这块是玻璃」。
       */
      boxShadow: `inset 0 0 0 1px rgba(255, 255, 255, 0.5),
                  inset 0 1px 0 0 rgba(255, 255, 255, 0.75),
                  inset 0 -1px 0 0 rgba(15, 23, 42, 0.06),
                  0 0 2px 1px color-mix(in oklch, black, transparent 86%) inset,
                  0 2px 6px rgba(15, 23, 42, 0.06)`,
    }
  }
  if (!supportsBackdropFilter()) {
    return {
      ...base,
      background: 'rgba(255, 255, 255, 0.62)',
      border: '1px solid rgba(255, 255, 255, 0.6)',
      boxShadow: 'inset 0 1px 0 0 rgba(255,255,255,0.6), inset 0 -1px 0 0 rgba(15,23,42,0.06)',
    }
  }
  return {
    ...base,
    background: 'rgba(255, 255, 255, 0.42)',
    backdropFilter: 'blur(12px) saturate(1.6) brightness(1.04)',
    WebkitBackdropFilter: 'blur(12px) saturate(1.6) brightness(1.04)',
    border: '1px solid rgba(255, 255, 255, 0.55)',
    boxShadow: `0 8px 32px rgba(31, 38, 135, 0.14),
                0 2px 16px rgba(31, 38, 135, 0.08),
                inset 0 1px 0 0 rgba(255, 255, 255, 0.5)`,
  }
})

const updateFilterElements = () => {
  const els = [
    { ref: redChannelRef, offset: props.redOffset },
    { ref: greenChannelRef, offset: props.greenOffset },
    { ref: blueChannelRef, offset: props.blueOffset },
  ]
  for (const { ref: r, offset } of els) {
    if (r.value) {
      r.value.setAttribute('scale', (props.distortionScale + offset).toString())
      r.value.setAttribute('xChannelSelector', props.xChannel)
      r.value.setAttribute('yChannelSelector', props.yChannel)
    }
  }
  if (gaussianBlurRef.value) {
    gaussianBlurRef.value.setAttribute('stdDeviation', props.displace.toString())
  }
}

watch(
  () => [
    props.width,
    props.height,
    props.borderRadius,
    props.borderWidth,
    props.brightness,
    props.opacity,
    props.blur,
    props.displace,
    props.distortionScale,
    props.redOffset,
    props.greenOffset,
    props.blueOffset,
    props.xChannel,
    props.yChannel,
    props.mixBlendMode,
  ],
  () => {
    updateDisplacementMap()
    updateFilterElements()
  },
)

onMounted(() => {
  nextTick(() => {
    updateDisplacementMap()
    updateFilterElements()
    if (containerRef.value && typeof ResizeObserver !== 'undefined') {
      resizeObserver = new ResizeObserver(() => setTimeout(updateDisplacementMap, 0))
      resizeObserver.observe(containerRef.value)
    }
  })
})

onUnmounted(() => {
  resizeObserver?.disconnect()
  resizeObserver = null
})
</script>

<template>
  <div ref="containerRef" class="glass-surface" :style="containerStyles">
    <svg class="glass-defs" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <defs>
        <filter :id="filterId" color-interpolation-filters="sRGB" x="0%" y="0%" width="100%" height="100%">
          <feImage ref="feImageRef" x="0" y="0" width="100%" height="100%" preserveAspectRatio="none" result="map" />
          <feDisplacementMap ref="redChannelRef" in="SourceGraphic" in2="map" result="dispRed" />
          <feColorMatrix
            in="dispRed"
            type="matrix"
            values="1 0 0 0 0
                    0 0 0 0 0
                    0 0 0 0 0
                    0 0 0 1 0"
            result="red"
          />
          <feDisplacementMap ref="greenChannelRef" in="SourceGraphic" in2="map" result="dispGreen" />
          <feColorMatrix
            in="dispGreen"
            type="matrix"
            values="0 0 0 0 0
                    0 1 0 0 0
                    0 0 0 0 0
                    0 0 0 1 0"
            result="green"
          />
          <feDisplacementMap ref="blueChannelRef" in="SourceGraphic" in2="map" result="dispBlue" />
          <feColorMatrix
            in="dispBlue"
            type="matrix"
            values="0 0 0 0 0
                    0 0 0 0 0
                    0 0 1 0 0
                    0 0 0 1 0"
            result="blue"
          />
          <feBlend in="red" in2="green" mode="screen" result="rg" />
          <feBlend in="rg" in2="blue" mode="screen" result="output" />
          <feGaussianBlur ref="gaussianBlurRef" in="output" stdDeviation="0.7" />
        </filter>
      </defs>
    </svg>

    <div class="glass-inner" :style="{ padding: innerPadding }">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.glass-surface {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  transition: opacity 0.26s ease-out;
}
/* 滤镜定义用的 svg 本身不参与布局/绘制 */
.glass-defs {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  opacity: 0;
  z-index: -1;
}
.glass-inner {
  position: relative;
  z-index: 10;
  display: flex;
  width: 100%;
  height: 100%;
  min-width: 0;
}
</style>

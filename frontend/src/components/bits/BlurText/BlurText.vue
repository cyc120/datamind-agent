<template>
  <p ref="rootRef" class="flex flex-wrap blur-text" :class="className">
    <Motion
      v-for="(segment, index) in elements"
      :key="index"
      tag="span"
      :initial="fromSnapshot"
      :animate="inView ? buildKeyframes(fromSnapshot, toSnapshots) : fromSnapshot"
      :transition="getTransition(index)"
      @animation-complete="handleAnimationComplete(index)"
      style="display: inline-block; will-change: transform, filter, opacity"
    >
      {{ segment === ' ' ? '\u00A0' : segment }}
      <template v-if="animateBy === 'words' && index < elements.length - 1">&nbsp;</template>
    </Motion>
  </p>
</template>

<script setup lang="ts">
import { Motion, type Transition } from 'motion-v';
import { computed, onBeforeUnmount, onMounted, ref, useTemplateRef, watch } from 'vue';

type BlurTextProps = {
  text?: string;
  delay?: number;
  className?: string;
  animateBy?: 'words' | 'letters';
  direction?: 'top' | 'bottom';
  threshold?: number;
  rootMargin?: string;
  animationFrom?: Record<string, string | number>;
  animationTo?: Array<Record<string, string | number>>;
  easing?: (t: number) => number;
  onAnimationComplete?: () => void;
  stepDuration?: number;
};

const buildKeyframes = (
  from: Record<string, string | number>,
  steps: Array<Record<string, string | number>>
): Record<string, Array<string | number>> => {
  const keys = new Set<string>([...Object.keys(from), ...steps.flatMap(s => Object.keys(s))]);

  const keyframes: Record<string, Array<string | number>> = {};
  keys.forEach(k => {
    keyframes[k] = [from[k], ...steps.map(s => s[k])];
  });
  return keyframes;
};

const props = withDefaults(defineProps<BlurTextProps>(), {
  text: '',
  delay: 200,
  className: '',
  animateBy: 'words',
  direction: 'top',
  threshold: 0.1,
  rootMargin: '0px',
  easing: (t: number) => t,
  stepDuration: 0.35
});

const inView = ref(false);
const rootRef = useTemplateRef<HTMLParagraphElement>('rootRef');
let observer: IntersectionObserver | null = null;
// ── 本地修改（非上游代码）────────────────────────────────────────────────────
// 上游只在 IntersectionObserver 回调里把 inView 置真，元素初始 opacity 为 0，
// 因此观察器不回调时标题会永久不可见（无头截图、预览抓取等环境实测如此）。
// 加一个兜底定时器：到点仍未进入视口判定就照常播放动画，不改变动画本身。
let fallback: ReturnType<typeof setTimeout> | null = null;
const BLUR_TEXT_FALLBACK_MS = 1200;

const startFallback = () => {
  if (fallback !== null) clearTimeout(fallback);
  fallback = setTimeout(() => {
    inView.value = true;
  }, BLUR_TEXT_FALLBACK_MS);
};

onMounted(() => {
  if (!rootRef.value) return;

  // 降低动效偏好下不等待滚动触发，直接进入终态。
  if (typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches) {
    inView.value = true;
    return;
  }

  observer = new IntersectionObserver(
    ([entry]) => {
      if (entry.isIntersecting) {
        inView.value = true;
        observer?.unobserve(rootRef.value as Element);
      }
    },
    {
      threshold: props.threshold,
      rootMargin: props.rootMargin
    }
  );

  observer.observe(rootRef.value);
  startFallback();
});

onBeforeUnmount(() => {
  observer?.disconnect();
  if (fallback !== null) clearTimeout(fallback);
});

watch(
  () => [props.text, props.animateBy, props.direction, props.delay],
  () => {
    inView.value = false;
    if (rootRef.value) {
      observer?.observe(rootRef.value);
      startFallback();
    }
  }
);

const elements = computed(() => (props.animateBy === 'words' ? props.text.split(' ') : props.text.split('')));

// ── 本地修改（非上游代码）────────────────────────────────────────────────────
// 降低动效偏好时直接呈现终态：不给逐词模糊动画，也就不存在「动画未播放完 =
// 文字停在 opacity:0」的中间态。这既是无障碍的正确语义，也让静态预览可读。
const prefersReduced =
  typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;

const defaultFrom = computed(() =>
  props.direction === 'top' ? { filter: 'blur(10px)', opacity: 0, y: -50 } : { filter: 'blur(10px)', opacity: 0, y: 50 }
);

const defaultTo = computed(() => [
  {
    filter: 'blur(5px)',
    opacity: 0.5,
    y: props.direction === 'top' ? 5 : -5
  },
  {
    filter: 'blur(0px)',
    opacity: 1,
    y: 0
  }
]);

const toSnapshots = computed(() => props.animationTo ?? defaultTo.value);
const finalSnapshot = computed(() => toSnapshots.value[toSnapshots.value.length - 1]);

const fromSnapshot = computed(() =>
  prefersReduced ? finalSnapshot.value : props.animationFrom ?? defaultFrom.value
);

const stepCount = computed(() => toSnapshots.value.length + 1);
const totalDuration = computed(() => props.stepDuration * (stepCount.value - 1));

const times = computed(() =>
  Array.from({ length: stepCount.value }, (_, i) => (stepCount.value === 1 ? 0 : i / (stepCount.value - 1)))
);

const getTransition = (index: number): Transition => ({
  duration: totalDuration.value,
  times: times.value,
  delay: (index * props.delay) / 1000,
  ease: props.easing
});

const handleAnimationComplete = (index: number) => {
  if (index === elements.value.length - 1) {
    props.onAnimationComplete?.();
  }
};
</script>

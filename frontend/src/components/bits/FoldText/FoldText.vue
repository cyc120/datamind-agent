<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted, type CSSProperties } from 'vue';
import { gsap } from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';

gsap.registerPlugin(ScrollTrigger);

export type SplitBy = 'char' | 'word' | 'line';
export type Hinge = 'top' | 'bottom' | 'left' | 'right';
export type Trigger = 'mount' | 'hover' | 'scroll' | 'loop';

interface HingeConfig {
  origin: string;
  rotateX: number;
  rotateY: number;
}

const HINGE_CONFIG: Record<Hinge, HingeConfig> = {
  top: { origin: '50% 0%', rotateX: -92, rotateY: 0 },
  bottom: { origin: '50% 100%', rotateX: 92, rotateY: 0 },
  left: { origin: '0% 50%', rotateX: 0, rotateY: 92 },
  right: { origin: '100% 50%', rotateX: 0, rotateY: -92 }
};

const CREASE_GRADIENTS: Record<Hinge, string> = {
  top: 'linear-gradient(180deg, rgba(0, 0, 0, 0.58) 0%, rgba(0, 0, 0, 0.22) 42%, rgba(255, 255, 255, 0.26) 100%)',
  bottom: 'linear-gradient(0deg, rgba(0, 0, 0, 0.58) 0%, rgba(0, 0, 0, 0.22) 42%, rgba(255, 255, 255, 0.26) 100%)',
  left: 'linear-gradient(90deg, rgba(0, 0, 0, 0.58) 0%, rgba(0, 0, 0, 0.22) 42%, rgba(255, 255, 255, 0.26) 100%)',
  right: 'linear-gradient(270deg, rgba(0, 0, 0, 0.58) 0%, rgba(0, 0, 0, 0.22) 42%, rgba(255, 255, 255, 0.26) 100%)'
};

const clamp = (value: number, min: number, max: number): number => Math.min(max, Math.max(min, value));

interface FoldTextProps {
  text?: string;
  splitBy?: SplitBy;
  hinge?: Hinge;
  duration?: number;
  stagger?: number;
  ease?: string;
  perspective?: number;
  creaseShading?: number;
  trigger?: Trigger;
  fontSize?: string | number;
  fontWeight?: string | number;
  color?: string;
}

interface SegmentData {
  content: string;
  split: SplitBy;
}

type RenderItem =
  | { type: 'br'; key: string }
  | { type: 'whitespace'; key: string; text: string }
  | { type: 'segment'; key: string; data: SegmentData };

const props = withDefaults(defineProps<FoldTextProps>(), {
  text: 'Design unfolds',
  splitBy: 'char',
  hinge: 'top',
  duration: 0.65,
  stagger: 0.045,
  ease: 'power3.out',
  perspective: 700,
  creaseShading: 0.55,
  trigger: 'mount',
  fontSize: 80,
  fontWeight: 800,
  color: '#f7f2e8'
});

const rootRef = ref<HTMLSpanElement | null>(null);

const hingeConfig = computed<HingeConfig>(() => HINGE_CONFIG[props.hinge] || HINGE_CONFIG.top);
const creaseGradient = computed(() => CREASE_GRADIENTS[props.hinge] || CREASE_GRADIENTS.top);
const safeCrease = computed(() => clamp(props.creaseShading, 0, 1));
const safePerspective = computed(() => Math.max(120, props.perspective));

const rootStyle = computed<CSSProperties>(() => ({
  fontSize: typeof props.fontSize === 'number' ? `${props.fontSize}px` : props.fontSize,
  fontWeight: props.fontWeight,
  color: props.color
}));

const items = computed<RenderItem[]>(() => {
  if (props.splitBy === 'line') {
    return props.text.split('\n').map((line, index) => ({
      type: 'segment' as const,
      key: `segment-line-${index}`,
      data: { content: line || '\u00A0', split: 'line' as SplitBy }
    }));
  }

  if (props.splitBy === 'word') {
    let segmentIndex = 0;
    const result: RenderItem[] = [];
    props.text.split(/(\s+)/).forEach((part, index) => {
      if (!part) return;
      if (/^\s+$/.test(part)) {
        part.split(/(\n)/).forEach((wsPart, wsIndex) => {
          if (wsPart === '\n') result.push({ type: 'br', key: `ws-${index}-br-${wsIndex}` });
          else if (wsPart)
            result.push({
              type: 'whitespace',
              key: `ws-${index}-space-${wsIndex}`,
              text: wsPart.replace(/ /g, '\u00A0')
            });
        });
        return;
      }
      segmentIndex += 1;
      result.push({
        type: 'segment',
        key: `segment-word-${segmentIndex}`,
        data: { content: part, split: 'word' }
      });
    });
    return result;
  }

  const result: RenderItem[] = [];
  Array.from(props.text).forEach((char, index) => {
    if (char === '\n') {
      result.push({ type: 'br', key: `br-${index}` });
      return;
    }
    result.push({
      type: 'segment',
      key: `segment-char-${index}`,
      data: { content: char === ' ' ? '\u00A0' : char, split: 'char' }
    });
  });
  return result;
});

let timeline: gsap.core.Timeline | null = null;
let teardownAnimation: (() => void) | null = null;

const setupAnimation = () => {
  if (typeof window === 'undefined') return;
  const root = rootRef.value;
  if (!root) return;

  const pieces = Array.from(root.querySelectorAll<HTMLElement>('.fold-text-piece'));
  if (!pieces.length) return;

  const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  const activeDuration = reduceMotion ? Math.min(props.duration, 0.22) : props.duration;
  const activeStagger = reduceMotion ? Math.min(props.stagger, 0.02) : props.stagger;
  const fromVars = {
    opacity: 0,
    rotateX: reduceMotion ? 0 : hingeConfig.value.rotateX,
    rotateY: reduceMotion ? 0 : hingeConfig.value.rotateY,
    '--fold-crease': reduceMotion ? 0 : safeCrease.value,
    transformOrigin: hingeConfig.value.origin,
    force3D: true
  };
  const toVars = {
    opacity: 1,
    rotateX: 0,
    rotateY: 0,
    '--fold-crease': 0,
    duration: activeDuration,
    ease: reduceMotion ? 'power1.out' : props.ease,
    stagger: activeStagger,
    clearProps: 'willChange'
  };

  const killTimeline = () => {
    timeline?.kill();
    timeline = null;
    gsap.killTweensOf(pieces);
  };

  const play = (repeat: boolean): gsap.core.Timeline => {
    killTimeline();
    timeline = gsap.timeline({ repeat: repeat ? -1 : 0, repeatDelay: repeat ? 0.75 : 0 });
    timeline.fromTo(pieces, fromVars, toVars);
    return timeline;
  };

  let scrollTrigger: ReturnType<typeof ScrollTrigger.create> | undefined;
  let hoverHandler: (() => void) | undefined;

  if (props.trigger === 'hover') {
    gsap.set(pieces, {
      opacity: 1,
      rotateX: 0,
      rotateY: 0,
      '--fold-crease': 0,
      transformOrigin: hingeConfig.value.origin
    });
    hoverHandler = () => play(false);
    root.addEventListener('mouseenter', hoverHandler);
  } else if (props.trigger === 'scroll') {
    gsap.set(pieces, fromVars);
    scrollTrigger = ScrollTrigger.create({
      trigger: root,
      start: 'top 82%',
      once: true,
      onEnter: () => play(false)
    });
  } else if (props.trigger === 'loop') {
    play(true);
  } else {
    play(false);
  }

  teardownAnimation = () => {
    if (hoverHandler) root.removeEventListener('mouseenter', hoverHandler);
    scrollTrigger?.kill();
    killTimeline();
  };
};

onMounted(() => {
  setupAnimation();
});

onUnmounted(() => {
  teardownAnimation?.();
  teardownAnimation = null;
});

watch(
  () => [
    props.text,
    props.splitBy,
    props.hinge,
    props.duration,
    props.stagger,
    props.ease,
    props.perspective,
    safeCrease.value,
    props.trigger,
    hingeConfig.value.origin,
    hingeConfig.value.rotateX,
    hingeConfig.value.rotateY
  ],
  () => {
    teardownAnimation?.();
    teardownAnimation = null;
    setupAnimation();
  },
  { flush: 'post' }
);
</script>

<template>
  <span
    ref="rootRef"
    class="inline-block leading-[0.95] tracking-[-0.04em] whitespace-pre-wrap select-text fold-text"
    :style="rootStyle"
  >
    <span class="sr-only">{{ text }}</span>
    <span class="inline" aria-hidden="true">
      <template v-for="item in items" :key="item.key">
        <br v-if="item.type === 'br'" />
        <span v-else-if="item.type === 'whitespace'" class="inline">{{ item.text }}</span>
        <span
          v-else
          class="fold-text-segment align-baseline leading-[inherit] transform-3d"
          :class="item.data.split === 'line' ? 'block' : 'inline-block'"
          :data-fold-split="item.data.split"
          :style="{ '--fold-perspective': `${safePerspective}px` } as CSSProperties"
        >
          <span
            class="inline-block after:absolute relative after:bg-(image:--fold-crease-bg) after:opacity-(--fold-crease,0) motion-reduce:after:opacity-0! after:rounded-[0.08em] fold-text-piece text-inherit after:content-[''] leading-[inherit] motion-reduce:transform-none! after:pointer-events-none will-change-[transform,opacity] transform-3d backface-hidden after:inset-[-0.08em_-0.02em] after:mix-blend-multiply"
            :data-fold-hinge="hinge"
            :style="
              {
                transformOrigin: hingeConfig.origin,
                '--fold-crease': 0,
                '--fold-crease-bg': creaseGradient
              } as CSSProperties
            "
          >
            {{ item.data.content }}
          </span>
        </span>
      </template>
    </span>
  </span>
</template>

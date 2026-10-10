<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount } from 'vue'

const props = withDefaults(
  defineProps<{
    autoFlip?: boolean // 进入视口后自动翻面
    flipOnHover?: boolean
    flipDelayMs?: number
  }>(),
  { autoFlip: false, flipOnHover: false, flipDelayMs: 400 },
)

const flipped = ref(false)
const root = ref<HTMLElement | null>(null)
let observer: IntersectionObserver | null = null

onMounted(() => {
  if (!props.autoFlip) return
  observer = new IntersectionObserver(
    (entries) => {
      if (entries[0].isIntersecting) {
        setTimeout(() => (flipped.value = true), props.flipDelayMs)
        observer?.disconnect()
      }
    },
    { threshold: 0.5 },
  )
  if (root.value) observer.observe(root.value)
})
onBeforeUnmount(() => observer?.disconnect())
</script>

<template>
  <div
    ref="root"
    class="flip"
    :class="{ flipped }"
    @click="flipped = !flipped"
    @mouseenter="flipOnHover && (flipped = true)"
    @mouseleave="flipOnHover && (flipped = false)"
  >
    <div class="flip-inner">
      <div class="flip-face flip-front glass">
        <slot name="front" />
      </div>
      <div class="flip-face flip-back">
        <slot name="back" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.flip {
  perspective: 1600px;
  cursor: pointer;
  height: 100%;
}
.flip-inner {
  position: relative;
  width: 100%;
  height: 100%;
  transition: transform 0.9s cubic-bezier(0.2, 0.85, 0.3, 1);
  transform-style: preserve-3d;
}
.flip.flipped .flip-inner {
  transform: rotateY(180deg);
}
.flip-face {
  position: absolute;
  inset: 0;
  backface-visibility: hidden;
  -webkit-backface-visibility: hidden;
  border-radius: 24px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
}
.flip-back {
  transform: rotateY(180deg);
  background: linear-gradient(160deg, rgba(56, 189, 248, 0.16), rgba(129, 140, 248, 0.12));
  border: 1px solid rgba(129, 140, 248, 0.35);
}
</style>

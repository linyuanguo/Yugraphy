<template>
  <span>{{ display }}</span>
</template>

<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'

/** KPI 大屏数字：数值变化时带滚动动画（roll=false 则直接显示最终值） */
const props = withDefaults(
  defineProps<{ value: number; roll?: boolean; duration?: number }>(),
  { roll: true, duration: 650 },
)

function fmt(v: number) {
  return (v || 0).toLocaleString('zh-CN')
}

const display = ref(fmt(props.value))
let raf = 0
let lastShown = props.value

function animate(from: number, to: number) {
  if (raf) cancelAnimationFrame(raf)
  if (!props.roll || Math.abs(to - from) < 2) {
    display.value = fmt(to)
    lastShown = to
    return
  }
  const start = performance.now()
  const dur = Math.min(props.duration, 1200)
  const tick = (t: number) => {
    const p = Math.min(1, (t - start) / dur)
    const eased = 1 - Math.pow(1 - p, 3)
    const cur = Math.round(from + (to - from) * eased)
    display.value = fmt(cur)
    if (p < 1) {
      raf = requestAnimationFrame(tick)
    } else {
      lastShown = to
    }
  }
  raf = requestAnimationFrame(tick)
}

watch(
  () => props.value,
  (to, from) => animate(typeof from === 'number' ? from : lastShown, to),
)

onBeforeUnmount(() => {
  if (raf) cancelAnimationFrame(raf)
})
</script>

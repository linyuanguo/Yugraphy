<template>
  <svg
    class="spark"
    :viewBox="`0 0 ${VW} ${VH}`"
    :width="width"
    :height="height"
    preserveAspectRatio="none"
  >
    <defs>
      <linearGradient :id="gradId" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" :stop-color="color" stop-opacity="0.38" />
        <stop offset="100%" :stop-color="color" stop-opacity="0.02" />
      </linearGradient>
    </defs>
    <polygon v-if="area" :points="area" :fill="`url(#${gradId})`" />
    <polyline
      v-if="line"
      :points="line"
      fill="none"
      :stroke="color"
      stroke-width="1.6"
      stroke-linejoin="round"
      stroke-linecap="round"
      vector-effect="non-scaling-stroke"
    />
    <circle
      v-if="lastPoint"
      class="spark-dot"
      :cx="lastPoint.x"
      :cy="lastPoint.y"
      r="1.8"
      :fill="color"
    />
  </svg>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    /** 采样值序列（旧 → 新） */
    points?: number[]
    color?: string
    width?: number
    height?: number
  }>(),
  { points: () => [], color: '#1677ff', width: 120, height: 34 },
)

const VW = 100
const VH = 30
let seq = 0
const gradId = `spark-grad-${(seq += 1)}-${Math.random().toString(36).slice(2, 8)}`

const pts = computed(() => (props.points || []).filter((n) => Number.isFinite(n)))

/** 归一化到 viewBox 坐标（上下留 2 单位边距；全平时画中线） */
const coords = computed(() => {
  const arr = pts.value
  if (arr.length < 2) return [] as { x: number; y: number }[]
  const min = Math.min(...arr)
  const max = Math.max(...arr)
  const span = max - min
  const step = VW / (arr.length - 1)
  return arr.map((v, i) => ({
    x: +(i * step).toFixed(2),
    y: span <= 0 ? VH / 2 : +(VH - 2 - ((v - min) / span) * (VH - 4)).toFixed(2),
  }))
})

const line = computed(() => coords.value.map((p) => `${p.x},${p.y}`).join(' '))

const area = computed(() => {
  const c = coords.value
  if (!c.length) return ''
  return `0,${VH} ${line.value} ${VW},${VH}`
})

const lastPoint = computed(() => coords.value[coords.value.length - 1] || null)
</script>

<style scoped>
.spark {
  display: block;
  overflow: visible;
}
.spark-dot {
  animation: spark-pulse 1.6s ease-in-out infinite;
}
@keyframes spark-pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.25;
  }
}
</style>
